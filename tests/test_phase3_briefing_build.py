from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.models import (
    Base,
    Briefing,
    CollectionJob,
    Document,
    DocumentVersion,
    Evidence,
    Source,
    StrategyCandidate,
)
from scalping_briefing.publishing.briefing_build import build_briefing


CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)
SETTINGS = {
    "TIMEZONE": "Asia/Seoul",
    "initial_lookback_days": 14,
    "max_lookback_days": 30,
    "briefing_max_items": 7,
    "quote_max_chars": 300,
}


def _candidate(
    version: DocumentVersion,
    candidate_id: str,
    status: str,
    created_at: datetime,
) -> tuple[StrategyCandidate, list[Evidence]]:
    candidate = StrategyCandidate(
        candidate_id=candidate_id,
        canonical_name=f"Strategy {candidate_id}",
        summary="A bounded strategy summary.",
        review_status=status,
        created_at=created_at,
        core_hypothesis="The documented condition matters.",
        signal_inputs=["quotes"],
        entry_logic="Enter after confirmation.",
        exit_logic="Exit on reversal.",
        required_data=["quotes"],
        risk_notes="Latency risk.",
        field_status={field: "explicit" for field in CORE_FIELDS},
        relevance_status="relevant",
        value_score=80,
        document_version_ids=[version.document_version_id],
    )
    evidence = [
        Evidence(
            evidence_id=f"{candidate_id}-{field}",
            document_version=version,
            strategy_candidate=candidate,
            field_name=field,
            quote=f"Evidence for {field}.",
            section_or_locator=field,
        )
        for field in CORE_FIELDS
    ]
    return candidate, evidence


def _database(tmp_path: Path) -> tuple[object, Session, DocumentVersion]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = Source(
        source_id="source-1",
        name="Fixture source",
        type="fixture",
        base_url="https://example.invalid",
        connector_type="fixture",
        active=True,
    )
    document = Document(
        document_id="document-1",
        source=source,
        canonical_url="https://example.invalid/document",
        title="Fixture document",
    )
    version = DocumentVersion(
        document_version_id="version-1",
        document=document,
        content_hash="content-hash-1",
    )
    session.add_all([source, document, version])
    session.flush()
    return engine, session, version


def test_two_schedules_archive_distinct_briefings_with_adjacent_windows(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        approved, approved_evidence = _candidate(
            version, "approved-1", "approved", datetime(2026, 8, 3, tzinfo=UTC)
        )
        pending, pending_evidence = _candidate(
            version, "pending-1", "needs_review", datetime(2026, 8, 3, tzinfo=UTC)
        )
        session.add_all([approved, pending, *approved_evidence, *pending_evidence])
        session.commit()

        first = build_briefing(
            session,
            scheduled_for=datetime(2026, 8, 4, 8, tzinfo=UTC),
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        second = build_briefing(
            session,
            scheduled_for=datetime(2026, 8, 7, 8, tzinfo=UTC),
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        assert first.briefing_id != second.briefing_id
        assert first.window_end == second.window_start
        assert first.markdown_location is not None
        assert Path(first.markdown_location).is_file()
        assert any(item.carried_over for item in second.items)
    finally:
        session.close()
        engine.dispose()


def test_retry_reuses_briefing_id_and_increments_attempt(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        candidate, evidence = _candidate(
            version, "retry-1", "approved", datetime(2026, 8, 3, tzinfo=UTC)
        )
        session.add_all([candidate, *evidence])
        session.commit()
        scheduled_for = datetime(2026, 8, 4, 8, tzinfo=UTC)
        first = build_briefing(
            session,
            scheduled_for=scheduled_for,
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        first.run_status = "failed"
        session.commit()

        retry = build_briefing(
            session,
            scheduled_for=scheduled_for,
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        assert retry.briefing_id == first.briefing_id
        assert retry.run_attempt == 2
        assert session.scalar(select(Briefing).where(Briefing.briefing_id == first.briefing_id)) is not None
        assert len(session.scalars(select(Briefing)).all()) == 1
    finally:
        session.close()
        engine.dispose()


def test_empty_or_failed_source_window_is_successfully_reported(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        session.add(
            CollectionJob(
                collection_job_id="failed-job",
                source_id="source-1",
                scheduled_for=datetime(2026, 8, 4, 8, tzinfo=UTC),
                status="failed",
                error="fixture source unavailable",
            )
        )
        session.commit()

        briefing = build_briefing(
            session,
            scheduled_for=datetime(2026, 8, 4, 8, tzinfo=UTC),
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        body = Path(briefing.markdown_location).read_text(encoding="utf-8")

        assert briefing.run_status == "success"
        assert briefing.approved_count == 0
        assert briefing.source_summary["failed"] == 1
        assert "적격 신규 자료 없음" in body
        assert "일부 출처 수집 실패" in body
    finally:
        session.close()
        engine.dispose()


def test_partially_evidenced_queue_candidate_does_not_fail_the_whole_briefing(
    tmp_path, monkeypatch
) -> None:
    """One unsupported field must not take the scheduled briefing down.

    A queued candidate that carries Evidence for some core fields and leaves
    the rest unknown is normal review material.  The briefing has to build,
    publish only the evidenced claims, and leave the unsupported field empty.
    """

    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    unsupported = {"entry_logic", "exit_logic"}
    try:
        # Built without the unsupported Evidence rows at all: creating an
        # Evidence object already links it to its candidate, so filtering the
        # list afterwards would leave the relationship in place.
        partial = StrategyCandidate(
            candidate_id="partial-1",
            canonical_name="Strategy partial-1",
            summary="A bounded strategy summary.",
            review_status="needs_review",
            created_at=datetime(2026, 8, 3, tzinfo=UTC),
            core_hypothesis="The documented condition matters.",
            signal_inputs=["quotes"],
            entry_logic=None,
            exit_logic=None,
            required_data=["quotes"],
            risk_notes="Latency risk.",
            field_status={
                field: "unknown" if field in unsupported else "explicit"
                for field in CORE_FIELDS
            },
            relevance_status="relevant",
            value_score=80,
            document_version_ids=[version.document_version_id],
        )
        supported = [
            Evidence(
                evidence_id=f"partial-1-{field}",
                document_version=version,
                strategy_candidate=partial,
                field_name=field,
                quote=f"Evidence for {field}.",
                section_or_locator=field,
            )
            for field in CORE_FIELDS
            if field not in unsupported
        ]
        session.add_all([partial, *supported])
        session.commit()

        briefing = build_briefing(
            session,
            scheduled_for=datetime(2026, 8, 4, 8, tzinfo=UTC),
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        assert briefing.markdown_location is not None
        assert Path(briefing.markdown_location).is_file()
        assert [item.strategy_candidate_id for item in briefing.items] == ["partial-1"]
        body = Path(briefing.markdown_location).read_text(encoding="utf-8")
        assert "Evidence for core_hypothesis." in body
        assert "Evidence for entry_logic." not in body
        assert "partial-1-entry_logic" not in body
    finally:
        session.close()
        engine.dispose()
