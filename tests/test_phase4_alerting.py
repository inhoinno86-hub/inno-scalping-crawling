from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scalping_briefing.ops.alerting import emit_metric_alerts
from scalping_briefing.ops.metrics import MetricResult, ObservationWindow


START = datetime(2026, 8, 3, tzinfo=UTC)
END = START + timedelta(days=7)
WINDOW = ObservationWindow(start=START, end=END, timezone="UTC")


def _metrics() -> list[MetricResult]:
    return [
        MetricResult(
            "M1",
            "Collection success",
            0.5,
            0.95,
            "breached",
            1,
            2,
            2,
            detail={"source_text": "do not serialize this content"},
        ),
        MetricResult("M2", "Briefing delay", None, 30, "insufficient_data", 0, 0, 0),
        MetricResult("M3", "Review backlog", 2, 20, "meets_target", 2, 1, 2),
    ]


def test_metric_alerts_write_breaches_and_insufficient_data_separately(
    tmp_path: Path,
) -> None:
    paths = emit_metric_alerts(WINDOW, _metrics(), alerts_dir=tmp_path)

    assert len(paths) == 2
    assert {path.name for path in paths} == {
        f"{WINDOW.window_id}:M1.json",
        f"{WINDOW.window_id}:M2.json",
    }

    payloads = {
        json.loads(path.read_text(encoding="utf-8"))["event"]: json.loads(
            path.read_text(encoding="utf-8")
        )
        for path in paths
    }
    breach = payloads["metric_breach:M1"]
    assert breach["alert_id"] == f"{WINDOW.window_id}:M1"
    assert breach["severity"] == "error"
    assert breach["details"] == {
        "value": 0.5,
        "target": 0.95,
        "window": WINDOW.window_id,
        "numerator": 1,
        "denominator": 2,
    }

    insufficient = payloads["metric_insufficient_data:M2"]
    assert insufficient["alert_id"] == f"{WINDOW.window_id}:M2"
    assert insufficient["severity"] == "warning"
    assert insufficient["details"] == {
        "value": None,
        "target": 30,
        "window": WINDOW.window_id,
        "numerator": 0,
        "denominator": 0,
    }
    assert "source_text" not in breach["details"]
    assert "do not serialize this content" not in json.dumps(payloads)


def test_metric_alert_ids_are_deterministic_and_repeated_calls_do_not_multiply(
    tmp_path: Path,
) -> None:
    first = emit_metric_alerts(WINDOW, _metrics(), alerts_dir=tmp_path)
    second = emit_metric_alerts(WINDOW, _metrics(), alerts_dir=tmp_path)

    assert second == first
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert {
        json.loads(path.read_text(encoding="utf-8"))["alert_id"]
        for path in second
    } == {f"{WINDOW.window_id}:M1", f"{WINDOW.window_id}:M2"}
