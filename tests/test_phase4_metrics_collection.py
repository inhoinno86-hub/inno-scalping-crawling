from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.models import Base, Briefing, CollectionJob, DocumentVersion, Source
from scalping_briefing.ops.metrics import (
    M1_TARGET_SUCCESS_RATE,
    M2_TARGET_DELAY_MINUTES,
    M5_TARGET_DUPLICATE_RATE,
    MetricResult,
    ObservationWindow,
    calculate_m1_collection_success_rate,
    calculate_m2_briefing_delay,
    calculate_m5_duplicate_rate,
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
    """Small query-result double for duplicate/retry records impossible in schema."""

    def __init__(self, records: dict[type[object], list[object]]) -> None:
        self.records = records
        self.no_autoflush = nullcontext()

    def scalars(self, statement) -> _ScalarResult:
        entity = statement.column_descriptions[0]["entity"]
        return _ScalarResult(self.records.get(entity, []))


def _database() -> tuple[object, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_observation_window_and_metric_result_are_deterministic_structures() -> None:
    same = ObservationWindow(start=START, end=END, timezone="UTC")
    different = ObservationWindow(
        start=START + timedelta(hours=1), end=END, timezone="UTC"
    )

    assert same.window_id == WINDOW.window_id
    assert same.window_id != different.window_id
    assert same.start == START
    assert same.end == END
    assert same.timezone == "UTC"

    result = MetricResult(
        metric_id="M-test",
        title="Test metric",
        value=1,
        target=1,
        verdict="meets_target",
        numerator=1,
        denominator=1,
        sample_size=1,
        detail={"window_id": WINDOW.window_id},
    )
    assert result.as_dict()["metric_id"] == "M-test"
    assert result.as_dict()["detail"]["window_id"] == WINDOW.window_id


def test_m1_counts_only_terminal_jobs_from_active_sources() -> None:
    engine, session = _database()
    try:
        active = Source(
            source_id="active-source",
            name="Active",
            type="fixture",
            base_url="fixture://active",
            connector_type="fixture",
            active=True,
        )
        inactive = Source(
            source_id="inactive-source",
            name="Inactive",
            type="fixture",
            base_url="fixture://inactive",
            connector_type="fixture",
            active=False,
        )
        in_window = START + timedelta(hours=1)
        jobs = [
            CollectionJob(
                collection_job_id="success",
                source=active,
                status="success",
                scheduled_for=in_window,
                completed_at=in_window,
                terminal_error=False,
            ),
            CollectionJob(
                collection_job_id="terminal-failure",
                source=active,
                status="failed",
                scheduled_for=in_window,
                completed_at=in_window,
                terminal_error=True,
            ),
            CollectionJob(
                collection_job_id="retry-failure",
                source=active,
                status="failed",
                scheduled_for=in_window,
                completed_at=in_window,
                terminal_error=False,
            ),
            CollectionJob(
                collection_job_id="inactive-success",
                source=inactive,
                status="success",
                scheduled_for=in_window,
                completed_at=in_window,
                terminal_error=False,
            ),
            CollectionJob(
                collection_job_id="outside-success",
                source=active,
                status="success",
                scheduled_for=END + timedelta(hours=1),
                completed_at=END + timedelta(hours=1),
                terminal_error=False,
            ),
        ]
        session.add_all([active, inactive, *jobs])
        session.commit()
        before = [
            (job.collection_job_id, job.status, job.terminal_error)
            for job in session.scalars(select(CollectionJob)).all()
        ]

        result = calculate_m1_collection_success_rate(session, WINDOW)

        assert result.metric_id == "M1"
        assert result.numerator == 1
        assert result.denominator == 2
        assert result.sample_size == 2
        assert result.value == 0.5
        assert result.target == M1_TARGET_SUCCESS_RATE
        assert result.verdict == "breached"
        after = [
            (job.collection_job_id, job.status, job.terminal_error)
            for job in session.scalars(select(CollectionJob)).all()
        ]
        assert after == before
    finally:
        session.close()
        engine.dispose()


def test_m2_uses_latest_successful_attempt_and_judges_maximum_delay() -> None:
    scheduled = START + timedelta(days=1)
    records = [
        Briefing(
            briefing_id="briefing-1",
            scheduled_for=scheduled,
            generated_at=scheduled + timedelta(minutes=10),
            run_attempt=1,
            run_status="success",
            window_start=START,
            window_end=END,
        ),
        Briefing(
            briefing_id="briefing-1",
            scheduled_for=scheduled,
            generated_at=scheduled + timedelta(minutes=90),
            run_attempt=2,
            run_status="success",
            window_start=START,
            window_end=END,
        ),
        Briefing(
            briefing_id="briefing-1",
            scheduled_for=scheduled,
            generated_at=scheduled + timedelta(minutes=120),
            run_attempt=3,
            run_status="failed",
            window_start=START,
            window_end=END,
        ),
        Briefing(
            briefing_id="briefing-2",
            scheduled_for=scheduled,
            generated_at=scheduled + timedelta(minutes=20),
            run_attempt=1,
            run_status="success",
            window_start=START,
            window_end=END,
        ),
    ]
    session = _ReadOnlyRecords({Briefing: records})

    result = calculate_m2_briefing_delay(session, WINDOW)

    assert result.metric_id == "M2"
    assert result.sample_size == 2
    assert result.value == 90
    assert result.detail["maximum"] == 90
    assert result.detail["median"] == 55
    assert result.target == M2_TARGET_DELAY_MINUTES
    assert result.verdict == "breached"


def test_m5_counts_repeated_hash_only_within_same_document() -> None:
    records = [
        DocumentVersion(
            document_version_id="doc-1-v1",
            document_id="doc-1",
            version_no=1,
            content_hash="same",
            created_at=START - timedelta(days=1),
        ),
        DocumentVersion(
            document_version_id="doc-1-v2",
            document_id="doc-1",
            version_no=2,
            content_hash="same",
            created_at=START + timedelta(hours=1),
        ),
        DocumentVersion(
            document_version_id="doc-1-v3",
            document_id="doc-1",
            version_no=3,
            content_hash="new",
            created_at=START + timedelta(hours=2),
        ),
        DocumentVersion(
            document_version_id="doc-2-v1",
            document_id="doc-2",
            version_no=1,
            content_hash="same",
            created_at=START + timedelta(hours=3),
        ),
    ]
    session = _ReadOnlyRecords({DocumentVersion: records})

    result = calculate_m5_duplicate_rate(session, WINDOW)

    assert result.metric_id == "M5"
    assert result.numerator == 1
    assert result.denominator == 3
    assert result.sample_size == 3
    assert result.value == 1 / 3
    assert result.detail["duplicate_version_ids"] == ["doc-1-v2"]
    assert result.target == M5_TARGET_DUPLICATE_RATE
    assert result.verdict == "breached"


def test_zero_sample_collection_metrics_are_insufficient_data() -> None:
    session = _ReadOnlyRecords({CollectionJob: [], Briefing: [], DocumentVersion: []})

    results = [
        calculate_m1_collection_success_rate(session, WINDOW),
        calculate_m2_briefing_delay(session, WINDOW),
        calculate_m5_duplicate_rate(session, WINDOW),
    ]

    assert all(result.verdict == "insufficient_data" for result in results)
    assert all(result.meets_target is False for result in results)
    assert all(result.value is None for result in results)
    assert all(result.sample_size == 0 for result in results)
