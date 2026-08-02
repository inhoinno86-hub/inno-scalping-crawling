"""Local failure-alert artifacts, kept separate from delivery channels."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .logging_setup import mask_secrets


def write_alert(
    event: str,
    message: str,
    *,
    severity: str = "error",
    details: dict[str, Any] | None = None,
    alerts_dir: str | Path = "alerts/",
    alert_id: str | None = None,
) -> Path:
    """Write one masked JSON failure artifact and return its path."""

    target_dir = Path(alerts_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    identifier = alert_id or uuid4().hex
    payload = {
        "alert_id": identifier,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "severity": severity,
        "message": message,
        "details": details or {},
    }
    safe_payload = mask_secrets(payload)
    target = target_dir / f"{identifier}.json"
    target.write_text(
        json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def record_failure(
    event: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    severity: str = "error",
    alerts_dir: str | Path = "alerts/",
) -> Path:
    """Named convenience API for collection/pipeline failure handlers."""

    return write_alert(
        event,
        message,
        severity=severity,
        details=details,
        alerts_dir=alerts_dir,
    )


write_failure_alert = write_alert
emit_failure_alert = record_failure


__all__ = [
    "emit_failure_alert",
    "record_failure",
    "write_alert",
    "write_failure_alert",
]
