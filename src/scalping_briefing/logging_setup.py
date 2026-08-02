"""JSON logging with recursive secret redaction."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, TextIO


REDACTED = "[REDACTED]"
_SECRET_KEY_RE = re.compile(r"(?:^|_)(?:TOKEN|KEY|SECRET)$", re.IGNORECASE)
_EMBEDDED_SECRET_RE = re.compile(
    r"(?P<key>REVIEW_API_TOKEN|TELEGRAM_BOT_TOKEN|[A-Za-z0-9]+_(?:TOKEN|KEY|SECRET))"
    r"(?P<sep>\s*[:=]\s*)(?P<value>[^\s,;]+)",
    re.IGNORECASE,
)


def is_secret_key(key: object) -> bool:
    """Return true for protected config/log field names."""

    normalized = str(key).upper()
    return normalized in {"REVIEW_API_TOKEN", "TELEGRAM_BOT_TOKEN"} or bool(
        _SECRET_KEY_RE.search(normalized)
    )


def _mask_text(value: str, secret_values: set[str]) -> str:
    masked = value
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        masked = masked.replace(secret, REDACTED)

    def replace(match: re.Match[str]) -> str:
        return f"{match.group('key')}{match.group('sep')}{REDACTED}"

    return _EMBEDDED_SECRET_RE.sub(replace, masked)


def mask_secrets(
    value: Any,
    *,
    key: object | None = None,
    secret_values: Iterable[str] | None = None,
) -> Any:
    """Recursively mask secret-keyed values and known secret strings."""

    known = {str(item) for item in (secret_values or ()) if item}
    if key is not None and is_secret_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {
            item_key: mask_secrets(item_value, key=item_key, secret_values=known)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [mask_secrets(item, secret_values=known) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_secrets(item, secret_values=known) for item in value)
    if isinstance(value, str):
        return _mask_text(value, known)
    return value


def _secret_values_from_mapping(value: Mapping[object, Any]) -> set[str]:
    secrets: set[str] = set()
    for key, item in value.items():
        if is_secret_key(key) and isinstance(item, str) and item:
            secrets.add(item)
        if isinstance(item, Mapping):
            secrets.update(_secret_values_from_mapping(item))
    return secrets


class SecretMaskingFilter(logging.Filter):
    """Filter that redacts secret values before a record reaches a handler."""

    def __init__(self, secret_values: Iterable[str] | None = None) -> None:
        super().__init__()
        self.secret_values = {str(item) for item in (secret_values or ()) if item}

    def filter(self, record: logging.LogRecord) -> bool:
        record_secrets = _secret_values_from_mapping(record.__dict__)
        known = self.secret_values | record_secrets
        record.msg = mask_secrets(record.msg, secret_values=known)
        record.args = mask_secrets(record.args, secret_values=known)
        for key, value in list(record.__dict__.items()):
            if key.startswith("_"):
                continue
            record.__dict__[key] = mask_secrets(value, key=key, secret_values=known)
        record.__dict__["_secret_values"] = known
        return True


class JsonFormatter(logging.Formatter):
    """Serialize one log record as one JSON object."""

    _standard_fields = set(logging.LogRecord(None, 0, "", 0, "", (), None).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        known = set(getattr(record, "_secret_values", set()))
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_secrets(record.getMessage(), secret_values=known),
        }
        for key, value in record.__dict__.items():
            if key in self._standard_fields or key.startswith("_"):
                continue
            payload[key] = mask_secrets(value, key=key, secret_values=known)
        if record.exc_info:
            payload["exception"] = mask_secrets(
                self.formatException(record.exc_info), secret_values=known
            )
        return json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)


def configure_logging(
    *,
    level: int | str = logging.INFO,
    stream: TextIO | None = None,
    secrets: Iterable[str] | Mapping[str, Any] | None = None,
    logger: logging.Logger | None = None,
) -> logging.Logger:
    """Install a single JSON stream handler and return configured logger."""

    target = logger or logging.getLogger()
    if isinstance(level, str):
        level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    if isinstance(secrets, Mapping):
        secret_values = _secret_values_from_mapping(secrets)
    else:
        secret_values = {str(item) for item in (secrets or ()) if item}
    secret_values.update(
        value
        for key, value in os.environ.items()
        if is_secret_key(key) and value
    )

    for handler in list(target.handlers):
        if getattr(handler, "_scalping_json_handler", False):
            target.removeHandler(handler)
            handler.close()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler._scalping_json_handler = True  # type: ignore[attr-defined]
    handler.addFilter(SecretMaskingFilter(secret_values))
    handler.setFormatter(JsonFormatter())
    target.addHandler(handler)
    target.setLevel(level)
    target.propagate = False
    return target


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """Emit a structured event without allowing secret fields through."""

    logger.log(level, event, extra=fields)


setup_logging = configure_logging
SecretMaskFilter = SecretMaskingFilter


__all__ = [
    "JsonFormatter",
    "REDACTED",
    "SecretMaskFilter",
    "SecretMaskingFilter",
    "configure_logging",
    "is_secret_key",
    "log_event",
    "mask_secrets",
    "setup_logging",
]
