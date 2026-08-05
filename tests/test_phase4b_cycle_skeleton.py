from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from scalping_briefing.orchestration.cycle import (
    STAGE_NAMES,
    CycleSummary,
    StageFailure,
    run_cycle,
    run_stage,
)


def test_stage_names_and_summary_serialization_are_fixed_and_deterministic() -> None:
    assert STAGE_NAMES == (
        "collect",
        "classify",
        "extract",
        "validate",
        "evidence",
        "score",
        "novelty",
        "route",
        "briefing",
        "gate",
        "delivery",
        "metrics",
        "report",
        "alerting",
    )

    summary = CycleSummary(
        scheduled_for="2026-08-07T08:00:00+09:00",
        trigger_type="scheduled",
        metrics={"M2": "insufficient_data", "M1": "meets_target"},
        failures=[StageFailure("extract", "document-1", "bad input")],
    )

    payload = summary.to_payload()
    assert set(payload) == {
        "phase",
        "status",
        "llm_mode",
        "delivery_mode",
        "scheduled_for",
        "trigger_type",
        "briefing_id",
        "stages",
        "briefing_generated",
        "delivery_invoked",
        "delivery_status",
        "metrics",
        "report_path",
        "alerts_written",
        "failures",
    }
    assert summary.status == "partial_success"
    assert summary.exit_code == 1
    assert CycleSummary().exit_code == 0
    assert CycleSummary(status="failed").exit_code == 1
    assert summary.to_json() == summary.to_json()
    assert json.loads(summary.to_json()) == payload


def test_run_stage_isolates_failure_records_masked_bounded_alert_and_continues(
    tmp_path: Path,
) -> None:
    summary = CycleSummary()
    default = object()

    result = run_stage(
        summary,
        "extract",
        "document-1",
        lambda: (_ for _ in ()).throw(
            RuntimeError("TELEGRAM_BOT_TOKEN=secret-token " + "x" * 300)
        ),
        alerts_dir=tmp_path,
        default=default,
    )
    assert result is default
    assert summary.stages["extract"].processed == 1
    assert summary.stages["extract"].succeeded == 0
    assert summary.stages["extract"].failed == 1
    assert summary.failures[0].reason.startswith("TELEGRAM_BOT_TOKEN=[REDACTED]")
    assert len(summary.failures[0].reason) <= 200

    assert run_stage(
        summary,
        "extract",
        "document-2",
        lambda: "ok",
        alerts_dir=tmp_path,
    ) == "ok"
    assert summary.stages["extract"].processed == 2
    assert summary.stages["extract"].succeeded == 1
    assert summary.stages["extract"].failed == 1

    artifacts = list(tmp_path.glob("*.json"))
    assert len(artifacts) == 1
    artifact_text = artifacts[0].read_text(encoding="utf-8")
    assert "secret-token" not in artifact_text
    assert "[REDACTED]" in artifact_text
    assert json.loads(artifact_text)["event"] == "cycle.extract"


def test_run_cycle_calculates_missing_schedule_and_leaves_unwired_stages_zero() -> None:
    now = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
    settings = SimpleNamespace(
        WEEKLY_REPORT_SCHEDULE=["TUE 08:00", "FRI 08:00"],
        TIMEZONE="Asia/Seoul",
        LLM_MODE="fixture",
        DELIVERY_MODE="dry_run",
        alerts_dir="alerts/",
    )

    summary = run_cycle(None, settings=settings, now=now)

    assert summary.scheduled_for == "2026-08-04T08:00:00+09:00"
    assert summary.trigger_type == "scheduled"
    assert summary.briefing_id
    assert summary.exit_code == 0
    assert all(
        tally.to_payload() == {"processed": 0, "succeeded": 0, "failed": 0}
        for tally in summary.stages.values()
    )
