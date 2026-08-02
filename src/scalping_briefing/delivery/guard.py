"""Pure delivery idempotency and resend guards.

This module contains policy only.  It performs no I/O and deliberately does
not implement a provider connector.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class DeliveryGuardError(ValueError):
    """Base error for invalid delivery keys or resend attempts."""


class InvalidIdempotencyKey(DeliveryGuardError):
    """Raised when an idempotency key is not three safe components."""


class ResendRejected(DeliveryGuardError):
    """Raised when an idempotent delivery cannot be attempted again."""


class ResendApprovalRequired(ResendRejected):
    """Raised when a resend lacks reason or approving reviewer identity."""


@dataclass(frozen=True)
class DeliveryHistory:
    """Small immutable history value accepted by the pure guard functions."""

    status: str
    attempt_no: int = 1
    resend_reason: str | None = None
    resend_approved_by: str | None = None


def _component(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise InvalidIdempotencyKey(f"{name} must be a non-empty string")
    if ":" in value or any(character.isspace() for character in value):
        raise InvalidIdempotencyKey(
            f"{name} must not contain ':' or whitespace"
        )
    return value


def make_idempotency_key(
    briefing_id: str, channel: str, content_hash: str
) -> str:
    """Build ``{briefing_id}:{channel}:{content_hash}`` safely."""

    return ":".join(
        (
            _component(briefing_id, "briefing_id"),
            _component(channel, "channel"),
            _component(content_hash, "content_hash"),
        )
    )


build_idempotency_key = make_idempotency_key
idempotency_key_for = make_idempotency_key


def validate_idempotency_key(value: str) -> str:
    """Validate key shape and return the original key.

    The function does not recompute the key because callers may not have the
    briefing payload available at validation time.  It enforces exactly three
    non-empty, whitespace-free components, matching the JSON contract.
    """

    if not isinstance(value, str):
        raise InvalidIdempotencyKey("idempotency_key must be a string")
    parts = value.split(":")
    if len(parts) != 3:
        raise InvalidIdempotencyKey(
            "idempotency_key must have briefing_id:channel:content_hash shape"
        )
    make_idempotency_key(*parts)
    return value


def is_valid_idempotency_key(value: str) -> bool:
    """Return whether ``value`` satisfies the idempotency-key contract."""

    try:
        validate_idempotency_key(value)
    except InvalidIdempotencyKey:
        return False
    return True


def _field(record: object, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _records(history: object | Iterable[object] | None) -> list[object]:
    if history is None:
        return []
    if isinstance(history, Mapping) or hasattr(history, "status"):
        return [history]
    if isinstance(history, str):
        return [DeliveryHistory(status=history)]
    try:
        return list(history)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("history must be a delivery record or iterable") from exc


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def resend_is_approved(
    resend_reason: str | None, resend_approved_by: str | None
) -> bool:
    """Return true only when both explicit resend fields are filled."""

    return _filled(resend_reason) and _filled(resend_approved_by)


def can_resend(
    history: object | Iterable[object] | None,
    *,
    resend_reason: str | None = None,
    resend_approved_by: str | None = None,
) -> bool:
    """Check resend policy without touching a database or provider."""

    try:
        next_attempt_no(
            history,
            resend_reason=resend_reason,
            resend_approved_by=resend_approved_by,
        )
    except ResendRejected:
        return False
    return True


def next_attempt_no(
    history: object | Iterable[object] | None,
    *,
    resend_reason: str | None = None,
    resend_approved_by: str | None = None,
) -> int:
    """Return next attempt number or reject an unsafe resend.

    First delivery returns ``1``.  Any subsequent attempt, including a retry
    after a failed record, must carry both explicit resend fields because the
    persisted delivery contract requires those fields for ``attempt_no >= 2``.
    A prior ``success`` record is therefore rejected by default and can only
    advance when both fields are present.
    """

    records = _records(history)
    if not records:
        return 1

    attempt_numbers = [
        int(_field(record, "attempt_no", 1) or 1) for record in records
    ]
    current_attempt = max(attempt_numbers, default=1)
    has_success = any(
        str(_field(record, "status", "")).lower() == "success"
        for record in records
    )
    if not resend_is_approved(resend_reason, resend_approved_by):
        if has_success:
            raise ResendApprovalRequired(
                "successful idempotency key cannot be resent without "
                "resend_reason and resend_approved_by"
            )
        raise ResendRejected(
            "attempt_no > 1 requires resend_reason and resend_approved_by"
        )
    return current_attempt + 1


guard_resend = next_attempt_no
guard_delivery_attempt = next_attempt_no
allow_resend = can_resend


__all__ = [
    "DeliveryGuardError",
    "DeliveryHistory",
    "InvalidIdempotencyKey",
    "ResendApprovalRequired",
    "ResendRejected",
    "allow_resend",
    "build_idempotency_key",
    "can_resend",
    "guard_delivery_attempt",
    "guard_resend",
    "idempotency_key_for",
    "is_valid_idempotency_key",
    "make_idempotency_key",
    "next_attempt_no",
    "resend_is_approved",
    "validate_idempotency_key",
]
