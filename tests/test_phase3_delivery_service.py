from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.delivery.connector import DeliveryAttemptResult
from scalping_briefing.delivery.guard import ResendApprovalRequired
from scalping_briefing.delivery.service import deliver_briefing
from scalping_briefing.models import (
    Base,
    Briefing,
    BriefingItem,
    Delivery,
    Evidence,
    StrategyCandidate,
)
from scalping_briefing.publishing.briefing_gate import BriefingApprovalError


SETTINGS = {
    "quote_max_chars": 300,
    "DELIVERY_CHANNEL": "telegram",
    "publication_policy": "manual_approval",
}
ATTEMPTED_AT = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


class SpyConnector:
    channel = "telegram"

    def __init__(self) -> None:
        self.rendered: list[object] = []
        self.sent: list[tuple[str, bool]] = []

    def render(self, payload: object) -> str:
        self.rendered.append(payload)
        briefing_id = payload["briefing_id"]  # type: ignore[index]
        return f"# briefing\n{briefing_id}"

    def send(self, message: str, *, dry_run: bool) -> DeliveryAttemptResult:
        self.sent.append((message, dry_run))
        return DeliveryAttemptResult(
            channel=self.channel,
            content_hash=hashlib.sha256(message.encode("utf-8")).hexdigest(),
            attempted_at=ATTEMPTED_AT,
            status="success",
            provider_reference="provider-reference-1",
            error=None,
        )


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.info["test_engine"] = engine
    return session


def _briefing(*, review_status: str = "approved") -> Briefing:
    candidate = StrategyCandidate(
        candidate_id="candidate-1",
        canonical_name="Queue Momentum",
        summary="A bounded source-backed observation.",
        review_status=review_status,
        source_confidence=0.9,
        extraction_confidence=0.9,
        value_score=80,
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        document_version_id="document-version-1",
        strategy_candidate=candidate,
        field_name="summary",
        quote="A bounded source-backed observation.",
        section_or_locator="abstract",
        source_url="https://example.invalid/source",
    )
    briefing = Briefing(
        briefing_id="briefing-1",
        scheduled_for=ATTEMPTED_AT,
        window_start=datetime(2026, 7, 20, 8, tzinfo=UTC),
        window_end=ATTEMPTED_AT,
        run_status="success",
        publication_status="approved",
        generated_at=ATTEMPTED_AT,
        timezone="Asia/Seoul",
        source_summary={"total": 1, "success": 1, "failed": 0, "not_executed": 0},
        candidate_count=1,
        approved_count=1 if review_status == "approved" else 0,
    )
    briefing.items.append(
        BriefingItem(
            briefing_item_id="briefing-item-1",
            strategy_candidate=candidate,
            strategy_id="strategy-1",
            reason_included="approved source-backed candidate",
            rank=1,
            carried_over=False,
            evidence=[evidence],
        )
    )
    return briefing


def _add(session: Session, briefing: Briefing) -> None:
    session.add(briefing)
    session.flush()


def _close(session: Session) -> None:
    engine = session.info.pop("test_engine")
    session.close()
    engine.dispose()


def test_first_delivery_records_all_delivery_fields() -> None:
    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)
        connector = SpyConnector()

        delivery = deliver_briefing(
            session,
            briefing,
            connector=connector,
            settings=SETTINGS,
        )

        assert delivery is not None
        expected_hash = hashlib.sha256(
            b"# briefing\nbriefing-1"
        ).hexdigest()
        assert delivery.delivery_id
        assert delivery.briefing_id == "briefing-1"
        assert delivery.channel == "telegram"
        assert delivery.idempotency_key == (
            f"briefing-1:telegram:{expected_hash}"
        )
        assert delivery.content_hash == expected_hash
        assert delivery.attempt_no == 1
        assert delivery.resend_reason is None
        assert delivery.resend_approved_by is None
        assert delivery.attempted_at == ATTEMPTED_AT
        assert delivery.status == "success"
        assert delivery.provider_reference == "provider-reference-1"
        assert delivery.error is None
        assert connector.sent == [("# briefing\nbriefing-1", True)]
        assert session.scalar(select(Delivery)) is delivery
    finally:
        _close(session)


def test_duplicate_success_is_rejected_before_connector_calls() -> None:
    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)
        deliver_briefing(session, briefing, connector=SpyConnector(), settings=SETTINGS)
        connector = SpyConnector()

        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(session, briefing, connector=connector, settings=SETTINGS)

        assert connector.rendered == []
        assert connector.sent == []
    finally:
        _close(session)


def test_resend_reason_alone_is_rejected() -> None:
    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)
        deliver_briefing(session, briefing, connector=SpyConnector(), settings=SETTINGS)
        connector = SpyConnector()

        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(
                session,
                briefing,
                connector=connector,
                settings=SETTINGS,
                resend_reason="operator-reviewed",
            )
        assert connector.rendered == []
    finally:
        _close(session)


def test_resend_approved_by_alone_is_rejected() -> None:
    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)
        deliver_briefing(session, briefing, connector=SpyConnector(), settings=SETTINGS)
        connector = SpyConnector()

        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(
                session,
                briefing,
                connector=connector,
                settings=SETTINGS,
                resend_approved_by="reviewer-1",
            )
        assert connector.rendered == []
    finally:
        _close(session)


def test_two_part_resend_updates_unique_key_with_next_attempt_number() -> None:
    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)
        first = deliver_briefing(
            session, briefing, connector=SpyConnector(), settings=SETTINGS
        )

        second = deliver_briefing(
            session,
            briefing,
            connector=SpyConnector(),
            settings=SETTINGS,
            resend_reason="operator-reviewed",
            resend_approved_by="reviewer-1",
        )

        assert second is first
        assert second is not None
        assert second.attempt_no == 2
        assert second.resend_reason == "operator-reviewed"
        assert second.resend_approved_by == "reviewer-1"
        assert len(session.scalars(select(Delivery)).all()) == 1
    finally:
        _close(session)


def test_gate_rejection_does_not_call_connector() -> None:
    session = _session()
    try:
        briefing = _briefing(review_status="needs_review")
        _add(session, briefing)
        connector = SpyConnector()

        with pytest.raises(BriefingApprovalError):
            deliver_briefing(
                session,
                briefing,
                connector=connector,
                settings=SETTINGS,
            )

        assert connector.rendered == []
        assert connector.sent == []
        assert session.scalar(select(Delivery)) is None
    finally:
        _close(session)
