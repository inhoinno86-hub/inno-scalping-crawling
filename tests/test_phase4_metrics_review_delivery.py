from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from scalping_briefing.models import (
    Briefing,
    BriefingItem,
    Delivery,
    Evidence,
    StrategyCandidate,
)
from scalping_briefing.ops.metrics import (
    M3_TARGET_PENDING_REVIEWS,
    M4_TARGET_DELIVERY_FAILURE_RATE,
    M6_TARGET_EVIDENCE_GAP_RATE,
    ObservationWindow,
    calculate_m3_review_backlog,
    calculate_m4_delivery_failure_rate,
    calculate_m6_evidence_gap_rate,
    compute_all_metrics,
)


START = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
END = START + timedelta(days=7)
WINDOW = ObservationWindow(start=START, end=END, timezone="UTC")


class _ScalarResult:
    def __init__(self, records: list[object]) -> None:
        self._records = records

    def all(self) -> list[object]:
        return list(self._records)


class _ReadOnlyRecords:
    """Query-result double that makes mutation and network calls impossible."""

    def __init__(self, records: dict[type[object], list[object]]) -> None:
        self.records = records
        self.no_autoflush = nullcontext()

    def scalars(self, statement) -> _ScalarResult:
        entity = statement.column_descriptions[0]["entity"]
        return _ScalarResult(self.records.get(entity, []))


def _candidate(candidate_id: str, review_status: str) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=candidate_id,
        canonical_name=candidate_id,
        summary="A bounded test candidate.",
        review_status=review_status,
    )


def _delivery(
    delivery_id: str,
    briefing_id: str,
    channel: str,
    attempt_no: int,
    status: str,
    attempted_at: datetime,
) -> Delivery:
    return Delivery.for_briefing(
        delivery_id=delivery_id,
        briefing_id=briefing_id,
        channel=channel,
        content_hash=f"hash-{delivery_id}",
        attempt_no=attempt_no,
        resend_reason="operator-reviewed" if attempt_no > 1 else None,
        resend_approved_by="reviewer-1" if attempt_no > 1 else None,
        status=status,
        attempted_at=attempted_at,
    )


def _item(
    item_id: str,
    briefing: Briefing,
    *,
    core_claim: bool,
    evidence: list[Evidence] | None = None,
) -> BriefingItem:
    return BriefingItem(
        briefing_item_id=item_id,
        briefing=briefing,
        strategy_id=f"strategy-{item_id}",
        reason_included="test claim",
        rank=1,
        core_claim=core_claim,
        evidence=evidence or [],
    )


def test_m3_uses_window_end_snapshot_and_excludes_final_review_states() -> None:
    candidates = [
        _candidate("needs-1", "needs_review"),
        _candidate("needs-2", "needs_review"),
        _candidate("approved", "approved"),
        _candidate("rejected", "rejected"),
        _candidate("archived", "archived"),
    ]
    before = [(candidate.candidate_id, candidate.review_status) for candidate in candidates]

    result = calculate_m3_review_backlog(
        _ReadOnlyRecords({StrategyCandidate: candidates}),
        WINDOW,
    )

    assert result.metric_id == "M3"
    assert result.value == 2
    assert result.numerator == 2
    assert result.denominator == 1
    assert result.sample_size == 5
    assert result.target == M3_TARGET_PENDING_REVIEWS
    assert result.verdict == "meets_target"
    assert result.detail["snapshot_at"] == END
    assert [(candidate.candidate_id, candidate.review_status) for candidate in candidates] == before


def test_m4_selects_maximum_attempt_per_pair_and_counts_dry_run_success() -> None:
    attempted_at = START + timedelta(hours=1)
    deliveries = [
        _delivery("a-1", "briefing-a", "telegram", 1, "failed", attempted_at),
        _delivery("a-2", "briefing-a", "telegram", 2, "success", attempted_at + timedelta(minutes=1)),
        _delivery("b-1", "briefing-b", "telegram", 1, "success", attempted_at),
        _delivery("b-2", "briefing-b", "telegram", 2, "failed", attempted_at + timedelta(minutes=1)),
        _delivery("c-1", "briefing-c", "email", 1, "success", attempted_at),
        _delivery("outside", "briefing-outside", "telegram", 1, "failed", END),
    ]
    before = [
        (delivery.delivery_id, delivery.attempt_no, delivery.status)
        for delivery in deliveries
    ]

    result = calculate_m4_delivery_failure_rate(
        _ReadOnlyRecords({Delivery: deliveries}),
        WINDOW,
        delivery_mode="dry_run",
    )

    assert result.metric_id == "M4"
    assert result.numerator == 1
    assert result.denominator == 3
    assert result.sample_size == 3
    assert result.value == 1 / 3
    assert result.target == M4_TARGET_DELIVERY_FAILURE_RATE
    assert result.verdict == "breached"
    assert result.detail["delivery_mode"] == "dry_run"
    assert result.detail["DELIVERY_MODE"] == "dry_run"
    assert [(delivery.delivery_id, delivery.attempt_no, delivery.status) for delivery in deliveries] == before


def test_m6_counts_only_publish_target_core_claims_without_evidence() -> None:
    approved = Briefing(
        briefing_id="approved-briefing",
        scheduled_for=START + timedelta(hours=1),
        publication_status="approved",
    )
    published = Briefing(
        briefing_id="published-briefing",
        scheduled_for=START + timedelta(hours=2),
        publication_status="published",
    )
    draft = Briefing(
        briefing_id="draft-briefing",
        scheduled_for=START + timedelta(hours=3),
        publication_status="pending_approval",
    )
    outside = Briefing(
        briefing_id="outside-briefing",
        scheduled_for=END + timedelta(hours=1),
        publication_status="approved",
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        document_version_id="document-version-1",
        strategy_candidate_id="candidate-1",
        field_name="summary",
        quote="A bounded quote.",
        section_or_locator="abstract",
    )
    items = [
        _item("approved-with-evidence", approved, core_claim=True, evidence=[evidence]),
        _item("approved-without-evidence", approved, core_claim=True),
        _item("approved-non-core", approved, core_claim=False),
        _item("published-with-evidence", published, core_claim=True, evidence=[evidence]),
        _item("draft-without-evidence", draft, core_claim=True),
        _item("outside-without-evidence", outside, core_claim=True),
    ]
    before = [
        (item.briefing_item_id, item.core_claim, len(item.evidence)) for item in items
    ]

    result = calculate_m6_evidence_gap_rate(
        _ReadOnlyRecords({BriefingItem: items}),
        WINDOW,
    )

    assert result.metric_id == "M6"
    assert result.numerator == 1
    assert result.denominator == 3
    assert result.sample_size == 3
    assert result.value == 1 / 3
    assert result.target == M6_TARGET_EVIDENCE_GAP_RATE
    assert result.verdict == "breached"
    assert [
        (item.briefing_item_id, item.core_claim, len(item.evidence)) for item in items
    ] == before


def test_compute_all_metrics_has_fixed_order_and_zero_sample_contract() -> None:
    results = compute_all_metrics(_ReadOnlyRecords({}), WINDOW)

    assert len(results) == 6
    assert [result.metric_id for result in results] == [
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
    ]
    assert all(result.verdict == "insufficient_data" for result in results)
    assert all(result.meets_target is False for result in results)
    assert all(result.value is None for result in results)
    assert all(result.sample_size == 0 for result in results)
