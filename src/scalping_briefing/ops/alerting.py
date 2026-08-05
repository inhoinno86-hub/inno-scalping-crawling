"""Write local operator alerts for Phase 4 metric observations.

This module only turns already-calculated metric results into the existing
local alert artifacts.  It does not deliver alerts or perform any I/O beyond
the call to :func:`scalping_briefing.alerts.write_alert`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .. import alerts


VERDICT_BREACHED = "breached"
VERDICT_INSUFFICIENT_DATA = "insufficient_data"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _window_id(window: Any) -> str:
    if isinstance(window, str):
        identifier = window.strip()
    else:
        identifier = _field(window, "window_id")
        if identifier is None:
            identifier = _field(window, "id")
        identifier = str(identifier).strip() if identifier is not None else ""
    if not identifier:
        raise ValueError("window must provide a non-empty window_id")
    return identifier


def _records(metrics: Any) -> list[tuple[Any, Any]]:
    if metrics is None:
        return []
    if isinstance(metrics, Mapping):
        if _field(metrics, "metric_id") is not None:
            return [(None, metrics)]
        return list(metrics.items())
    if isinstance(metrics, (str, bytes, bytearray)):
        return [(None, metrics)]
    try:
        return [(None, metric) for metric in metrics]
    except TypeError:
        return [(None, metrics)]


def _metric_id(metric: Any, supplied_id: Any) -> str:
    value = _field(metric, "metric_id", supplied_id)
    identifier = str(value).strip().upper() if value is not None else ""
    if not identifier:
        raise ValueError("each metric must provide a non-empty metric_id")
    return identifier


def _alert_details(metric: Any, window_id: str) -> dict[str, Any]:
    """Return only bounded metric fields; never serialize metric detail/source data."""

    return {
        "value": _field(metric, "value"),
        "target": _field(metric, "target"),
        "window": window_id,
        "numerator": _field(metric, "numerator"),
        "denominator": _field(metric, "denominator"),
    }


def emit_metric_alerts(
    window: Any,
    metrics: Iterable[Any] | Mapping[str, Any] | Any,
    *,
    alerts_dir: str | Path = "alerts/",
) -> list[Path]:
    """Write one deterministic local alert artifact for each metric condition.

    ``breached`` results use error severity.  ``insufficient_data`` results
    use warning severity and are intentionally recorded separately from
    breaches.  Results meeting their target produce no artifact.
    """

    window_identifier = _window_id(window)
    written: list[Path] = []
    seen_metric_ids: set[str] = set()

    for supplied_id, metric in _records(metrics):
        identifier = _metric_id(metric, supplied_id)
        if identifier in seen_metric_ids:
            continue
        seen_metric_ids.add(identifier)

        verdict = str(_field(metric, "verdict", "")).strip().lower()
        if verdict == VERDICT_BREACHED:
            event = f"metric_breach:{identifier}"
            severity = "error"
            message = f"Metric {identifier} breached its target."
        elif verdict == VERDICT_INSUFFICIENT_DATA:
            event = f"metric_insufficient_data:{identifier}"
            severity = "warning"
            message = f"Metric {identifier} has insufficient data."
        else:
            continue

        written.append(
            alerts.write_alert(
                event,
                message,
                severity=severity,
                details=_alert_details(metric, window_identifier),
                alerts_dir=alerts_dir,
                alert_id=f"{window_identifier}:{identifier}",
            )
        )

    return written


# Stable short aliases for callers using the package-D alert boundary.
alert_metric_breaches = emit_metric_alerts
emit_metric_breach_alerts = emit_metric_alerts
emit_alerts = emit_metric_alerts
record_metric_alerts = emit_metric_alerts
write_metric_alerts = emit_metric_alerts


__all__ = [
    "VERDICT_BREACHED",
    "VERDICT_INSUFFICIENT_DATA",
    "alert_metric_breaches",
    "emit_alerts",
    "emit_metric_alerts",
    "emit_metric_breach_alerts",
    "record_metric_alerts",
    "write_metric_alerts",
]
