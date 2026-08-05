from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scalping_briefing.models import Base
from scalping_briefing.ops.metrics import ObservationWindow
from scalping_briefing.orchestration import cycle
from scalping_briefing.orchestration.cycle import run_cycle


SCHEDULED_FOR = datetime(2026, 8, 7, 8, tzinfo=UTC)
WINDOW = ObservationWindow(
    start=datetime(2026, 7, 24, 8, tzinfo=UTC),
    end=SCHEDULED_FOR,
    timezone="UTC",
)
SETTINGS = SimpleNamespace(
    TIMEZONE="UTC",
    LLM_MODE="fixture",
    DELIVERY_MODE="dry_run",
    alerts_dir="alerts/",
)


def _session() -> tuple[object, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _wire_briefing(monkeypatch, *, fail: bool = False) -> None:
    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])

    if fail:
        def build(*_args, **_kwargs):
            raise RuntimeError("briefing build failed")
    else:
        def build(*_args, **_kwargs):
            return SimpleNamespace(
                briefing_id="briefing-ops-hookup",
                window_start=WINDOW.start,
                window_end=WINDOW.end,
            )

    monkeypatch.setattr(cycle, "build_briefing", build)
    monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_: briefing)
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        cycle,
        "deliver_briefing",
        lambda *_args, **_kwargs: SimpleNamespace(status="success"),
    )


def test_run_cycle_computes_six_metrics_with_injected_window_and_archives_report(
    monkeypatch, tmp_path: Path
) -> None:
    engine, session = _session()
    captured: dict[str, object] = {}
    original_compute = cycle.compute_all_metrics

    def compute(session_arg, window_arg, **kwargs):
        captured.update(session=session_arg, window=window_arg, **kwargs)
        return original_compute(session_arg, window_arg, **kwargs)

    monkeypatch.setattr(cycle, "compute_all_metrics", compute)
    _wire_briefing(monkeypatch)

    report_dir = tmp_path / "reports"
    alerts_dir = tmp_path / "alerts"
    try:
        summary = run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            observation_window=WINDOW,
            report_output_dir=report_dir,
            alerts_dir=alerts_dir,
        )
    finally:
        session.close()
        engine.dispose()

    assert captured["session"] is session
    assert captured["window"] is WINDOW
    assert captured["settings"] is SETTINGS
    assert captured["delivery_mode"] == SETTINGS.DELIVERY_MODE
    assert summary.metrics == {
        metric_id: "insufficient_data"
        for metric_id in ("M1", "M2", "M3", "M4", "M5", "M6")
    }

    report_files = sorted(report_dir.glob("*.md"))
    assert len(report_files) == 1
    assert Path(summary.report_path) == report_files[0]
    report_text = report_files[0].read_text(encoding="utf-8")
    assert all(metric_id in report_text for metric_id in summary.metrics)
    assert len(summary.alerts_written) == 6
    assert summary.alerts_written == sorted(summary.alerts_written)
    assert all(Path(path).exists() for path in summary.alerts_written)


def test_repeated_window_overwrites_deterministic_metric_alerts(
    monkeypatch, tmp_path: Path
) -> None:
    engine, session = _session()
    _wire_briefing(monkeypatch)
    report_dir = tmp_path / "reports"
    alerts_dir = tmp_path / "alerts"
    try:
        first = run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            observation_window=WINDOW,
            report_output_dir=report_dir,
            alerts_dir=alerts_dir,
        )
        second = run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            observation_window=WINDOW,
            report_output_dir=report_dir,
            alerts_dir=alerts_dir,
        )
    finally:
        session.close()
        engine.dispose()

    alert_files = sorted(alerts_dir.glob("*.json"))
    assert len(first.alerts_written) == 6
    assert len(second.alerts_written) == 6
    assert first.alerts_written == second.alerts_written
    assert len(alert_files) == 6
    assert sorted(str(path) for path in alert_files) == second.alerts_written


def test_operational_stages_run_after_briefing_failure(
    monkeypatch, tmp_path: Path
) -> None:
    engine, session = _session()
    _wire_briefing(monkeypatch, fail=True)
    try:
        summary = run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            observation_window=WINDOW,
            report_output_dir=tmp_path / "reports",
            alerts_dir=tmp_path / "alerts",
        )
    finally:
        session.close()
        engine.dispose()

    assert summary.stages["briefing"].failed == 1
    assert summary.stages["gate"].processed == 0
    assert summary.stages["delivery"].processed == 0
    for stage in ("metrics", "report", "alerting"):
        assert summary.stages[stage].to_payload() == {
            "processed": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
        }
    assert summary.report_path is not None
    assert summary.metrics == {
        metric_id: "insufficient_data"
        for metric_id in ("M1", "M2", "M3", "M4", "M5", "M6")
    }
