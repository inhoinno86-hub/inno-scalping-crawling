"""Operator-driven briefing publication decisions.

`build_briefing` always leaves a freshly built briefing at `pending_approval`,
and the publication gate accepts only `approved`/`published` or an explicit
internal draft.  Nothing used to move a briefing between those two facts, so
dry-run delivery was unreachable no matter how many candidates an operator
approved.  These tests pin the operator path that closes it: an explicit
decision, then a delivery that reuses the already-built briefing instead of
rebuilding it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.models import (
    Base,
    Briefing,
    BriefingItem,
    Delivery,
    Evidence,
    StrategyCandidate,
)
from scalping_briefing.review.cli import main as review_cli
from scalping_briefing.review.service import ReviewService


SETTINGS = {
    "TIMEZONE": "Asia/Seoul",
    "DELIVERY_CHANNEL": "telegram",
    "DELIVERY_MODE": "dry_run",
    "briefing_language": "ko",
    "briefing_max_items": 7,
    "quote_max_chars": 300,
}


def _database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    return engine


def _deliverable_item() -> BriefingItem:
    """One approved, Evidence-backed item: an empty briefing has no delivery target."""

    candidate = StrategyCandidate(
        candidate_id="candidate-1",
        canonical_name="Queue Momentum",
        summary="A bounded source-backed observation.",
        core_hypothesis="A bounded source-backed observation.",
        core_hypothesis_status="explicit",
        field_status={"core_hypothesis": "explicit"},
        review_status="approved",
        source_confidence=0.9,
        extraction_confidence=0.9,
        value_score=80,
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        document_version_id="document-version-1",
        strategy_candidate=candidate,
        field_name="core_hypothesis",
        quote="A bounded source-backed observation.",
        section_or_locator="abstract",
        source_url="https://example.invalid/source",
    )
    return BriefingItem(
        briefing_item_id="briefing-item-1",
        strategy_candidate=candidate,
        reason_included="approved source-backed candidate",
        rank=1,
        carried_over=False,
        evidence=[evidence],
    )


def _briefing(session: Session, *, status: str = "pending_approval") -> Briefing:
    briefing = Briefing(
        briefing_id="briefing-1",
        scheduled_for=datetime(2026, 8, 4, 8, tzinfo=UTC),
        trigger_type="scheduled",
        window_start=datetime(2026, 7, 21, 8, tzinfo=UTC),
        window_end=datetime(2026, 8, 4, 8, tzinfo=UTC),
        run_status="success",
        publication_status=status,
        generated_at=datetime(2026, 8, 4, 8, tzinfo=UTC),
        timezone="Asia/Seoul",
        source_summary={"total": 1, "success": 1, "failed": 0, "not_executed": 0},
        candidate_count=1,
        approved_count=1,
    )
    briefing.items.append(_deliverable_item())
    session.add(briefing)
    session.flush()
    return briefing


def test_operator_decision_moves_the_briefing_out_of_pending_approval(tmp_path) -> None:
    engine = _database(tmp_path)
    try:
        with Session(engine) as session:
            _briefing(session)
            service = ReviewService(session)

            decision = service.record_briefing_decision(
                "briefing-1", "operator-1", "approved", "checked the queue"
            )
            session.commit()

            briefing = session.get(Briefing, "briefing-1")
            assert briefing.publication_status == "approved"
            assert decision["briefing_id"] == "briefing-1"
            assert decision["reviewer_id"] == "operator-1"
            assert decision["decision"] == "approved"
            assert decision["previous_publication_status"] == "pending_approval"
    finally:
        engine.dispose()


def test_briefing_decision_rejects_unusable_input(tmp_path) -> None:
    engine = _database(tmp_path)
    try:
        with Session(engine) as session:
            _briefing(session)
            session.commit()
            service = ReviewService(session)

            with pytest.raises(ValueError, match="reviewer_id"):
                service.record_briefing_decision("briefing-1", "  ", "approved")
            with pytest.raises(ValueError, match="briefing not found"):
                service.record_briefing_decision("missing", "operator-1", "approved")
            with pytest.raises(ValueError, match="publication decision"):
                service.record_briefing_decision("briefing-1", "operator-1", "published")

            session.rollback()
            assert session.get(Briefing, "briefing-1").publication_status == "pending_approval"
    finally:
        engine.dispose()


def test_an_already_decided_briefing_is_not_silently_redecided(tmp_path) -> None:
    engine = _database(tmp_path)
    try:
        with Session(engine) as session:
            _briefing(session, status="approved")
            service = ReviewService(session)

            with pytest.raises(ValueError, match="publication status"):
                service.record_briefing_decision("briefing-1", "operator-1", "approved")
    finally:
        engine.dispose()


def test_cli_decides_and_then_delivers_the_approved_briefing(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = _database(tmp_path)
    url = f"sqlite:///{tmp_path / 'review.db'}"
    try:
        with Session(engine) as session:
            briefing = _briefing(session)
            briefing.markdown_location = str(tmp_path / "briefing.md")
            Path(briefing.markdown_location).write_text(
                "# 브리핑\n\n- 승인 항목 없음\n", encoding="utf-8"
            )
            session.commit()

        assert review_cli(
            [
                "--db",
                url,
                "briefing-decide",
                "briefing-1",
                "--reviewer-id",
                "operator-1",
                "--decision",
                "approved",
            ]
        ) == 0
        assert '"approved"' in capsys.readouterr().out

        assert review_cli(["--db", url, "briefing-deliver", "briefing-1"]) == 0
        delivered = capsys.readouterr().out
        assert '"status": "success"' in delivered
        assert '"briefing_id": "briefing-1"' in delivered

        with Session(engine) as session:
            deliveries = list(session.scalars(select(Delivery)).all())
            assert len(deliveries) == 1
            assert deliveries[0].briefing_id == "briefing-1"
    finally:
        engine.dispose()


def test_cli_refuses_to_deliver_a_briefing_that_was_never_approved(tmp_path, capsys, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = _database(tmp_path)
    url = f"sqlite:///{tmp_path / 'review.db'}"
    try:
        with Session(engine) as session:
            _briefing(session)
            session.commit()

        assert review_cli(["--db", url, "briefing-deliver", "briefing-1"]) == 1
        assert "approved" in capsys.readouterr().out

        with Session(engine) as session:
            assert list(session.scalars(select(Delivery)).all()) == []
    finally:
        engine.dispose()
