"""The recorded fixtures must keep carrying the offline cycle to candidates.

Prompt hashes embed a per-row ``document_version_id``, so recordings keyed by
prompt hash alone silently stop matching the moment the database is rebuilt.
These tests pin the content-addressed replay path end to end: one offline run
over the fixture sources has to reach routing with real candidates, and a
repeated run must stay quiet instead of re-reporting finished documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.config import load_config
from scalping_briefing.models import Base, StrategyCandidate
from scalping_briefing.orchestration.cycle import run_cycle


@pytest.fixture
def offline_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cycle.db'}")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _run(session: Session, tmp_path: Path):
    return run_cycle(
        session,
        settings=load_config(),
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )


def test_recorded_fixtures_carry_the_offline_cycle_to_routed_candidates(
    offline_session: Session, tmp_path: Path
) -> None:
    summary = _run(offline_session, tmp_path)

    extract = summary.stages["extract"]
    assert extract.processed > 0
    assert extract.failed == 0, [
        failure.to_payload() for failure in summary.failures if failure.stage == "extract"
    ]
    assert summary.stages["validate"].succeeded == extract.succeeded
    assert summary.stages["route"].succeeded > 0

    candidates = list(offline_session.scalars(select(StrategyCandidate)).all())
    assert candidates
    assert all(candidate.review_status != "approved" for candidate in candidates)


def test_repeating_the_offline_cycle_skips_finished_documents_without_alerts(
    offline_session: Session, tmp_path: Path
) -> None:
    first = _run(offline_session, tmp_path)
    first_classify = first.stages["classify"].processed
    assert first_classify > 0

    alerts_before = sorted(path.name for path in (tmp_path / "alerts").glob("*.json"))

    second = _run(offline_session, tmp_path)

    assert second.stages["classify"].processed == 0
    assert second.stages["classify"].skipped >= first_classify
    assert second.stages["classify"].failed == 0
    assert [failure.stage for failure in second.failures] == ["gate"]

    # Only the one cycle-level failure of the second run may add an artifact:
    # metric alerts keep their deterministic {window_id}:{metric_id} names and
    # finished documents no longer produce one alert each.
    alerts_after = sorted(path.name for path in (tmp_path / "alerts").glob("*.json"))
    assert len(alerts_after) == len(alerts_before) + len(second.failures)
    metric_alerts_before = [name for name in alerts_before if ":" in name]
    metric_alerts_after = [name for name in alerts_after if ":" in name]
    assert metric_alerts_after == metric_alerts_before
