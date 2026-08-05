from __future__ import annotations

import json
import socket
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scalping_briefing.config import CONFIG_KEYS
from scalping_briefing.models import (
    Briefing,
    BriefingItem,
    CollectionJob,
    Delivery,
    DocumentVersion,
    Evidence,
    Source,
    StrategyCandidate,
)
from scalping_briefing.ops.alerting import emit_metric_alerts
from scalping_briefing.ops.expansion import (
    APPENDIX_A_RECALIBRATION_KEYS,
    EXPANSION_CANDIDATES,
    build_expansion_recommendations,
    evaluate_expansion,
    evaluate_four_week_expansion,
    recommend_threshold_recalibration,
)
from scalping_briefing.ops.metrics import (
    M1_TARGET_SUCCESS_RATE,
    M2_TARGET_DELAY_MINUTES,
    M3_TARGET_PENDING_REVIEWS,
    M4_TARGET_DELIVERY_FAILURE_RATE,
    M5_TARGET_DUPLICATE_RATE,
    M6_TARGET_EVIDENCE_GAP_RATE,
    MetricResult,
    ObservationWindow,
    compute_all_metrics,
)
from scalping_briefing.ops.report import archive_report, render_report
from scalping_briefing.publishing.phrase_lint import find_banned_phrases


START = datetime(2026, 8, 3, tzinfo=UTC)
END = START + timedelta(days=7)
WINDOW = ObservationWindow(start=START, end=END, timezone="UTC")


class _ScalarResult:
    def __init__(self, records: list[object]) -> None:
        self._records = records

    def all(self) -> list[object]:
        return list(self._records)


class _ReadOnlyRecords:
    """Direct model records behind a query-shaped, mutation-free session double."""

    def __init__(self, records: dict[type[object], list[object]]) -> None:
        self.records = records
        self.no_autoflush = nullcontext()

    def scalars(self, statement: Any) -> _ScalarResult:
        entity = statement.column_descriptions[0]["entity"]
        return _ScalarResult(self.records.get(entity, []))


def _sample_records() -> _ReadOnlyRecords:
    active_source = Source(
        source_id="phase4-source",
        name="Phase 4 fixture source",
        type="feed",
        base_url="https://example.invalid/feed",
        connector_type="fixture",
        active=True,
    )
    jobs = [
        CollectionJob(
            collection_job_id="job-success-1",
            source_id=active_source.source_id,
            status="success",
            scheduled_for=START + timedelta(hours=1),
            completed_at=START + timedelta(hours=1, minutes=2),
            terminal_error=False,
        ),
        CollectionJob(
            collection_job_id="job-success-2",
            source_id=active_source.source_id,
            status="success",
            scheduled_for=START + timedelta(hours=2),
            completed_at=START + timedelta(hours=2, minutes=2),
            terminal_error=False,
        ),
        CollectionJob(
            collection_job_id="job-failed",
            source_id=active_source.source_id,
            status="failed",
            scheduled_for=START + timedelta(hours=3),
            completed_at=START + timedelta(hours=3, minutes=2),
            terminal_error=True,
        ),
        CollectionJob(
            collection_job_id="job-retrying",
            source_id=active_source.source_id,
            status="failed",
            scheduled_for=START + timedelta(hours=4),
            completed_at=START + timedelta(hours=4, minutes=2),
            terminal_error=False,
        ),
        CollectionJob(
            collection_job_id="job-outside-window",
            source_id=active_source.source_id,
            status="success",
            scheduled_for=END,
            completed_at=END,
            terminal_error=False,
        ),
    ]

    briefings = [
        Briefing(
            briefing_id="briefing-delay-1",
            scheduled_for=START + timedelta(hours=1),
            generated_at=START + timedelta(hours=1, minutes=12),
            run_status="success",
            run_attempt=1,
        ),
        Briefing(
            briefing_id="briefing-delay-2",
            scheduled_for=START + timedelta(hours=2),
            generated_at=START + timedelta(hours=2, minutes=24),
            run_status="success",
            run_attempt=1,
        ),
    ]

    candidates = [
        StrategyCandidate(
            candidate_id="candidate-needs-review",
            canonical_name="Needs review",
            summary="Bounded candidate summary.",
            review_status="needs_review",
        ),
        StrategyCandidate(
            candidate_id="candidate-approved",
            canonical_name="Approved",
            summary="Bounded candidate summary.",
            review_status="approved",
        ),
        StrategyCandidate(
            candidate_id="candidate-rejected",
            canonical_name="Rejected",
            summary="Bounded candidate summary.",
            review_status="rejected",
        ),
    ]

    attempted_at = START + timedelta(hours=5)
    deliveries = [
        Delivery.for_briefing(
            delivery_id="delivery-a-1",
            briefing_id="briefing-a",
            channel="telegram",
            content_hash="briefing-a-content",
            attempt_no=1,
            status="failed",
            attempted_at=attempted_at,
        ),
        Delivery.for_briefing(
            delivery_id="delivery-a-2",
            briefing_id="briefing-a",
            channel="telegram",
            content_hash="briefing-a-content",
            attempt_no=2,
            resend_reason="operator-reviewed",
            resend_approved_by="reviewer-1",
            status="success",
            attempted_at=attempted_at + timedelta(minutes=1),
        ),
        Delivery.for_briefing(
            delivery_id="delivery-b-1",
            briefing_id="briefing-b",
            channel="telegram",
            content_hash="briefing-b-content",
            attempt_no=1,
            status="failed",
            attempted_at=attempted_at,
        ),
    ]

    versions = [
        DocumentVersion(
            document_version_id="version-before-window",
            document_id="document-1",
            version_no=1,
            content_hash="hash-a",
            created_at=START - timedelta(hours=1),
        ),
        DocumentVersion(
            document_version_id="version-duplicate",
            document_id="document-1",
            version_no=2,
            content_hash="hash-a",
            created_at=START + timedelta(hours=1),
        ),
        DocumentVersion(
            document_version_id="version-unique",
            document_id="document-1",
            version_no=3,
            content_hash="hash-b",
            created_at=START + timedelta(hours=2),
        ),
        DocumentVersion(
            document_version_id="version-same-hash-other-document",
            document_id="document-2",
            version_no=1,
            content_hash="hash-a",
            created_at=START + timedelta(hours=3),
        ),
    ]

    publication = Briefing(
        briefing_id="briefing-publication",
        scheduled_for=START + timedelta(hours=6),
        publication_status="approved",
        run_status="pending",
    )
    evidence = Evidence(
        evidence_id="phase4-evidence",
        document_version_id="version-unique",
        strategy_candidate_id="candidate-needs-review",
        field_name="summary",
        quote="A bounded evidence quote.",
        section_or_locator="summary",
    )
    items = [
        BriefingItem(
            briefing_item_id="item-with-evidence",
            briefing_id=publication.briefing_id,
            briefing=publication,
            strategy_id="strategy-with-evidence",
            reason_included="bounded test claim",
            rank=1,
            core_claim=True,
            evidence=[evidence],
        ),
        BriefingItem(
            briefing_item_id="item-without-evidence",
            briefing_id=publication.briefing_id,
            briefing=publication,
            strategy_id="strategy-without-evidence",
            reason_included="bounded test claim",
            rank=2,
            core_claim=True,
        ),
        BriefingItem(
            briefing_item_id="item-non-core",
            briefing_id=publication.briefing_id,
            briefing=publication,
            strategy_id="strategy-non-core",
            reason_included="bounded test claim",
            rank=3,
            core_claim=False,
        ),
    ]

    return _ReadOnlyRecords(
        {
            CollectionJob: jobs,
            Briefing: briefings,
            StrategyCandidate: candidates,
            Delivery: deliveries,
            DocumentVersion: versions,
            BriefingItem: items,
        }
    )


def _weekly_metrics(*, insufficient_metric: str | None = None) -> list[MetricResult]:
    metrics: list[MetricResult] = []
    for number in range(1, 7):
        metric_id = f"M{number}"
        insufficient = metric_id == insufficient_metric
        metrics.append(
            MetricResult(
                metric_id=metric_id,
                title=f"Metric {metric_id}",
                value=None if insufficient else 1,
                target=1,
                verdict="insufficient_data" if insufficient else "meets_target",
                numerator=None if insufficient else 1,
                denominator=0 if insufficient else 1,
                sample_size=0 if insufficient else 1,
            )
        )
    return metrics


def _weekly_observation(number: int, *, insufficient_metric: str | None = None) -> dict[str, object]:
    start = START + timedelta(days=number * 7)
    return {
        "window": ObservationWindow(start=start, end=start + timedelta(days=7), timezone="UTC"),
        "metrics": _weekly_metrics(insufficient_metric=insufficient_metric),
    }


def test_phase4_dod1_six_operational_metrics_are_computed_from_records() -> None:
    results = compute_all_metrics(_sample_records(), WINDOW, delivery_mode="dry_run")

    expected = {
        "M1": (2 / 3, M1_TARGET_SUCCESS_RATE, "breached", 2, 3, 3),
        "M2": (24.0, M2_TARGET_DELAY_MINUTES, "meets_target", 24.0, 1, 2),
        "M3": (1, M3_TARGET_PENDING_REVIEWS, "meets_target", 1, 1, 3),
        "M4": (0.5, M4_TARGET_DELIVERY_FAILURE_RATE, "breached", 1, 2, 2),
        "M5": (1 / 3, M5_TARGET_DUPLICATE_RATE, "breached", 1, 3, 3),
        "M6": (0.5, M6_TARGET_EVIDENCE_GAP_RATE, "breached", 1, 2, 2),
    }

    assert [result.metric_id for result in results] == list(expected)
    for result in results:
        value, target, verdict, numerator, denominator, sample_size = expected[result.metric_id]
        assert result.value == value
        assert result.target == target
        assert result.verdict == verdict
        assert result.numerator == numerator
        assert result.denominator == denominator
        assert result.sample_size == sample_size
        assert result.meets_target is (verdict == "meets_target")


def test_phase4_dod2_periodic_report_renders_all_metrics_with_window_and_targets(
    tmp_path: Path,
) -> None:
    metrics = compute_all_metrics(_sample_records(), WINDOW, delivery_mode="dry_run")
    rendered = render_report(
        WINDOW,
        metrics,
        report_id="phase4-dod2-report",
        generated_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        settings={"LLM_MODE": "fixture", "DELIVERY_MODE": "dry_run"},
    )

    assert rendered.metadata["window_id"] == WINDOW.window_id
    assert rendered.metadata["window_start"] == START.isoformat()
    assert rendered.metadata["window_end"] == END.isoformat()
    assert rendered.metadata["LLM_MODE"] == "fixture"
    assert rendered.metadata["DELIVERY_MODE"] == "dry_run"
    assert "목표 위반 목록" in rendered
    assert set(rendered.metadata["breached"]) == {"M1", "M4", "M5", "M6"}
    assert "insufficient_data 목록" in rendered
    for metric in metrics:
        assert f"| {metric.metric_id} |" in rendered
        assert str(metric.target) in rendered
        assert metric.verdict in rendered
        assert str(metric.sample_size) in rendered
    assert find_banned_phrases(str(rendered)) == ()

    archived = archive_report(rendered, output_dir=tmp_path / "reports")
    assert archived.parent == tmp_path / "reports"
    assert archived.read_text(encoding="utf-8") == rendered


def test_phase4_dod3_metric_breach_emits_operator_alert_separate_from_delivery_channel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metrics = compute_all_metrics(_sample_records(), WINDOW, delivery_mode="dry_run")
    delivery_calls: list[object] = []

    def blocked_socket(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("metric alert path attempted network access")

    def forbidden_delivery(*args: object, **kwargs: object) -> None:
        delivery_calls.append((args, kwargs))
        raise AssertionError("metric alert path invoked delivery channel")

    import scalping_briefing.delivery.service as delivery_service

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(delivery_service, "deliver_briefing", forbidden_delivery)

    alerts_dir = tmp_path / "alerts"
    paths = emit_metric_alerts(WINDOW, metrics, alerts_dir=alerts_dir)

    assert {path.name for path in paths} == {
        f"{WINDOW.window_id}:M1.json",
        f"{WINDOW.window_id}:M4.json",
        f"{WINDOW.window_id}:M5.json",
        f"{WINDOW.window_id}:M6.json",
    }
    assert all(path.parent == alerts_dir for path in paths)
    assert delivery_calls == []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["event"].startswith("metric_breach:")
        assert payload["severity"] == "error"
        assert set(payload["details"]) == {
            "value",
            "target",
            "window",
            "numerator",
            "denominator",
        }
        assert "channel" not in payload
        assert "telegram" not in json.dumps(payload, ensure_ascii=False)


def test_phase4_dod4_missing_observations_are_insufficient_data_not_passing() -> None:
    results = compute_all_metrics(_ReadOnlyRecords({}), WINDOW)

    assert [result.metric_id for result in results] == [
        "M1",
        "M2",
        "M3",
        "M4",
        "M5",
        "M6",
    ]
    assert all(result.value is None for result in results)
    assert all(result.verdict == "insufficient_data" for result in results)
    assert all(result.meets_target is False for result in results)
    assert all(result.sample_size == 0 for result in results)


def test_phase4_dod5_expansion_requires_four_consecutive_weeks_meeting_targets() -> None:
    eligible = evaluate_four_week_expansion(
        [_weekly_observation(number) for number in range(4)]
    )
    assert eligible.expansion_eligible is True
    assert eligible.reason == "meets_target"
    assert len(eligible.window_ids) == 4

    only_three_weeks = evaluate_four_week_expansion(
        [_weekly_observation(number) for number in range(3)]
    )
    assert only_three_weeks.expansion_eligible is False
    assert only_three_weeks.reason == "insufficient_data"
    assert only_three_weeks.blocked_windows
    assert all(
        blocker.window_id and blocker.metric_id and blocker.reason == "insufficient_data"
        for blocker in only_three_weeks.blocked_metrics
    )

    one_insufficient_week = evaluate_four_week_expansion(
        [
            _weekly_observation(0),
            _weekly_observation(1, insufficient_metric="M4"),
            _weekly_observation(2),
            _weekly_observation(3),
        ]
    )
    assert one_insufficient_week.expansion_eligible is False
    assert one_insufficient_week.reason == "insufficient_data"
    assert any(
        blocker.window_id == _weekly_observation(1)["window"].window_id
        and blocker.metric_id == "M4"
        and blocker.reason == "insufficient_data"
        for blocker in one_insufficient_week.blocked_metrics
    )


def test_phase4_dod6_threshold_recalibration_is_recommendation_only_and_config_unchanged() -> None:
    observations = [_weekly_observation(number) for number in range(4)]
    source_policy = {
        "sources": [
            {"source_id": "inactive-real-source", "fixture": False, "active": False}
        ]
    }
    config_path = Path("config/default.toml")
    config_before = config_path.read_bytes()
    config_keys_before = tuple(CONFIG_KEYS)

    decision = evaluate_four_week_expansion(observations)
    recommendations = build_expansion_recommendations(decision, source_policy)
    recalibration = recommend_threshold_recalibration(
        decision,
        proposed_values={"briefing_max_items": 9},
    )
    assessment = evaluate_expansion(
        observations,
        source_policy=source_policy,
        proposed_values={"briefing_max_items": 9},
    )

    assert set(recommendations) == set(EXPANSION_CANDIDATES)
    assert set(assessment.recommendations) == set(EXPANSION_CANDIDATES)
    assert all(
        item["recommendation"] in {"recommend", "hold"}
        for item in assessment.recommendations.values()
    )
    assert set(recalibration) == set(APPENDIX_A_RECALIBRATION_KEYS)
    assert set(assessment.recalibration) == set(APPENDIX_A_RECALIBRATION_KEYS)
    assert recalibration["briefing_max_items"]["recommended_value"] == 9
    assert all(item["changed"] is False for item in recalibration.values())
    assert all(item["changed"] is False for item in assessment.recalibration.values())
    assert config_path.read_bytes() == config_before
    assert tuple(CONFIG_KEYS) == config_keys_before
