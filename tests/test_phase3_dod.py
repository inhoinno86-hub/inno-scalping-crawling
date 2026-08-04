from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scalping_briefing.models import CollectionJob
from scalping_briefing.pipeline.briefing_cursor import advance_cursor
from scalping_briefing.publishing.briefing_build import build_briefing

from test_phase3_briefing_build import CORE_FIELDS, SETTINGS, _candidate, _database


def test_phase3_dod1_two_scheduled_runs_produce_distinct_briefings(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        candidate, evidence = _candidate(
            version,
            "dod1-approved",
            "approved",
            datetime(2026, 8, 3, tzinfo=UTC),
        )
        session.add_all([candidate, *evidence])
        session.commit()

        first_schedule = datetime(2026, 8, 4, 8, tzinfo=UTC)
        second_schedule = datetime(2026, 8, 7, 8, tzinfo=UTC)
        first = build_briefing(
            session,
            scheduled_for=first_schedule,
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        second = build_briefing(
            session,
            scheduled_for=second_schedule,
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        assert first.briefing_id != second.briefing_id
        assert first.window_end == second.window_start
    finally:
        session.close()
        engine.dispose()


def test_phase3_dod2_briefing_item_traces_to_source_evidence_and_review(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        candidate, evidence = _candidate(
            version,
            "dod2-approved",
            "approved",
            datetime(2026, 8, 3, tzinfo=UTC),
        )
        session.add_all([candidate, *evidence])
        session.commit()

        scheduled_for = datetime(2026, 8, 4, 8, tzinfo=UTC)
        briefing = build_briefing(
            session,
            scheduled_for=scheduled_for,
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        item = briefing.items[0]
        item_evidence = item.evidence[0]
        source_url = (
            item_evidence.source_url
            or item_evidence.document_version.document.canonical_url
        )
        document_version_id = item_evidence.document_version_id
        evidence_citation = item_evidence.quote
        review_status = item.strategy_candidate.review_status
        data_reference_interval = (briefing.window_start, briefing.window_end)

        assert source_url == version.document.canonical_url
        assert document_version_id == version.document_version_id
        assert item_evidence.field_name in CORE_FIELDS
        assert evidence_citation == "Evidence for core_hypothesis."
        assert review_status == "approved"
        assert data_reference_interval == (
            scheduled_for - timedelta(days=SETTINGS["initial_lookback_days"]),
            scheduled_for,
        )
        candidate_created_at = candidate.created_at
        if candidate_created_at.tzinfo is None:
            candidate_created_at = candidate_created_at.replace(tzinfo=UTC)
        assert data_reference_interval[0] <= candidate_created_at <= data_reference_interval[1]
    finally:
        session.close()
        engine.dispose()


def test_phase3_dod4_empty_or_failed_window_still_produces_reported_briefing(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        pending, pending_evidence = _candidate(
            version,
            "dod4-pending",
            "needs_review",
            datetime(2026, 8, 5, tzinfo=UTC),
        )
        session.add_all([pending, *pending_evidence])
        session.commit()

        empty_schedule = datetime(2026, 8, 4, 8, tzinfo=UTC)
        empty_briefing = build_briefing(
            session,
            scheduled_for=empty_schedule,
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        empty_body = Path(empty_briefing.markdown_location).read_text(encoding="utf-8")

        assert empty_briefing.run_status == "success"
        assert empty_briefing.approved_count == 0
        assert "적격 신규 자료 없음" in empty_body
        assert "적격 신규 자료 없음" in empty_briefing.source_summary["notes"]

        failed_schedule = datetime(2026, 8, 7, 8, tzinfo=UTC)
        session.add(
            CollectionJob(
                collection_job_id="dod4-failed-job",
                source_id="source-1",
                scheduled_for=failed_schedule,
                status="failed",
                error="fixture source unavailable",
            )
        )
        session.commit()

        failed_source_briefing = build_briefing(
            session,
            scheduled_for=failed_schedule,
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        failed_source_body = Path(
            failed_source_briefing.markdown_location
        ).read_text(encoding="utf-8")

        assert failed_source_briefing.run_status == "success"
        assert failed_source_briefing.source_summary["failed"] == 1
        assert "승인 대기" in failed_source_briefing.source_summary["notes"]
        assert "일부 출처 수집 실패" in failed_source_briefing.source_summary["notes"]
        assert "승인 대기 후보가 있습니다" in failed_source_body
        assert "일부 출처 수집 실패" in failed_source_body
    finally:
        session.close()
        engine.dispose()


def test_phase3_dod6_retry_reuses_same_briefing_id_and_advances_cursor_on_success(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    try:
        candidate, evidence = _candidate(
            version,
            "dod6-approved",
            "approved",
            datetime(2026, 8, 3, tzinfo=UTC),
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
        first_id = first.briefing_id
        first_attempt = first.run_attempt
        first.run_status = "failed"
        session.commit()

        retry = build_briefing(
            session,
            scheduled_for=scheduled_for,
            trigger_type="scheduled",
            settings=SETTINGS,
        )

        assert retry.briefing_id == first_id
        assert retry.run_attempt == first_attempt + 1

        failed_schedule = datetime(2026, 8, 7, 8, tzinfo=UTC)
        successful_schedule = datetime(2026, 8, 10, 8, tzinfo=UTC)
        previous_success = [
            {
                "window_end": retry.window_end,
                "run_status": "success",
            }
        ]
        failed_cursor = advance_cursor(
            previous_success,
            scheduled_for=failed_schedule,
            run_status="failed",
            initial_lookback_days=SETTINGS["initial_lookback_days"],
            max_lookback_days=SETTINGS["max_lookback_days"],
        )
        successful_cursor = advance_cursor(
            [
                *previous_success,
                {
                    "window_end": failed_schedule,
                    "run_status": "failed",
                },
            ],
            scheduled_for=successful_schedule,
            run_status="success",
            initial_lookback_days=SETTINGS["initial_lookback_days"],
            max_lookback_days=SETTINGS["max_lookback_days"],
        )

        assert failed_cursor.advanced is False
        assert failed_cursor.cursor["window_end"] == retry.window_end
        assert successful_cursor.advanced is True
        assert successful_cursor.window_start == retry.window_end
        assert successful_cursor.cursor["window_end"] == successful_schedule
    finally:
        session.close()
        engine.dispose()


def test_phase3_dod3_duplicate_delivery_is_rejected_by_idempotency_key() -> None:
    import pytest

    from scalping_briefing.delivery.guard import ResendApprovalRequired
    from scalping_briefing.delivery.service import deliver_briefing
    from test_phase3_delivery_service import (
        ATTEMPTED_AT,
        SETTINGS,
        SpyConnector,
        _add,
        _briefing,
        _close,
        _session,
    )

    session = _session()
    try:
        briefing = _briefing()
        _add(session, briefing)

        first = deliver_briefing(
            session,
            briefing,
            connector=SpyConnector(),
            settings=SETTINGS,
        )

        assert first is not None
        assert first.attempt_no == 1
        assert first.attempted_at == ATTEMPTED_AT
        first_idempotency_key = first.idempotency_key

        duplicate_connector = SpyConnector()
        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(
                session,
                briefing,
                connector=duplicate_connector,
                settings=SETTINGS,
            )
        assert duplicate_connector.rendered == []
        assert duplicate_connector.sent == []

        reason_only_connector = SpyConnector()
        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(
                session,
                briefing,
                connector=reason_only_connector,
                settings=SETTINGS,
                resend_reason="operator-reviewed",
            )
        assert reason_only_connector.rendered == []
        assert reason_only_connector.sent == []

        reviewer_only_connector = SpyConnector()
        with pytest.raises(ResendApprovalRequired):
            deliver_briefing(
                session,
                briefing,
                connector=reviewer_only_connector,
                settings=SETTINGS,
                resend_approved_by="reviewer-1",
            )
        assert reviewer_only_connector.rendered == []
        assert reviewer_only_connector.sent == []

        resend_connector = SpyConnector()
        resend = deliver_briefing(
            session,
            briefing,
            connector=resend_connector,
            settings=SETTINGS,
            resend_reason="operator-reviewed",
            resend_approved_by="reviewer-1",
        )

        assert resend is first
        assert resend.idempotency_key == first_idempotency_key
        assert resend.attempt_no == 2
        assert resend.resend_reason == "operator-reviewed"
        assert resend.resend_approved_by == "reviewer-1"
        assert resend_connector.sent == [("# briefing\nbriefing-1", True)]
    finally:
        _close(session)


def test_phase3_dod5_unapproved_candidate_is_carried_over_not_delivered(tmp_path, monkeypatch) -> None:
    import pytest

    from scalping_briefing.delivery.service import deliver_briefing
    from scalping_briefing.publishing.briefing_gate import BriefingApprovalError
    from test_phase3_delivery_service import (
        ATTEMPTED_AT,
        SETTINGS as DELIVERY_SETTINGS,
        SpyConnector,
        _close,
    )

    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    session.info["test_engine"] = engine
    try:
        candidate, evidence = _candidate(
            version,
            "dod5-unapproved",
            "needs_review",
            ATTEMPTED_AT,
        )
        session.add_all([candidate, *evidence])
        session.commit()

        first_schedule = ATTEMPTED_AT + timedelta(days=1)
        first = build_briefing(
            session,
            scheduled_for=first_schedule,
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        first_item = next(
            item
            for item in first.items
            if item.strategy_candidate.candidate_id == candidate.candidate_id
        )
        assert first_item.carried_over is False
        assert first_item.strategy_candidate.review_status == "needs_review"

        connector = SpyConnector()
        with pytest.raises(BriefingApprovalError):
            deliver_briefing(
                session,
                first,
                connector=connector,
                settings=DELIVERY_SETTINGS,
            )
        assert connector.rendered == []
        assert connector.sent == []
        assert first.deliveries == []

        second = build_briefing(
            session,
            scheduled_for=first_schedule + timedelta(days=3),
            trigger_type="scheduled",
            settings=SETTINGS,
        )
        carried = [
            item
            for item in second.items
            if item.strategy_candidate.candidate_id == candidate.candidate_id
        ]

        assert len(carried) == 1
        assert carried[0].carried_over is True
        assert carried[0].strategy_candidate.review_status == "needs_review"
    finally:
        _close(session)
