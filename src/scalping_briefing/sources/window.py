"""Bounded collection windows used by source connectors.

Window calculation is deliberately independent from transport and persistence.  A
connector can therefore use the same rules for a fixture response and a live
response, while callers can persist the returned window record with their run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping


DEFAULT_INITIAL_LOOKBACK_DAYS = 14
DEFAULT_MAX_LOOKBACK_DAYS = 30
UTC = timezone.utc


def _as_datetime(value: Any, *, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 datetime") from exc
    else:
        raise ValueError(f"{name} must be a datetime or ISO-8601 string")
    if result.tzinfo is None:
        return result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _positive_days(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return result


def _cursor_datetime(cursor: Any) -> datetime | None:
    if cursor is None:
        return None
    if isinstance(cursor, (datetime, date)):
        return _as_datetime(cursor, name="cursor")
    if isinstance(cursor, str):
        try:
            return _as_datetime(cursor, name="cursor")
        except ValueError:
            return None
    if isinstance(cursor, Mapping):
        for key in (
            "window_end",
            "last_success_at",
            "last_success",
            "updated_at",
            "timestamp",
        ):
            value = cursor.get(key)
            if value is not None:
                try:
                    return _as_datetime(value, name=key)
                except ValueError:
                    continue
    return None


@dataclass(frozen=True)
class CollectionWindow:
    """The actual bounded interval used by one collection attempt."""

    window_start: datetime
    window_end: datetime
    requested_start: datetime
    initial_lookback_days: int
    max_lookback_days: int
    truncated: bool = False

    @property
    def lookback_days(self) -> int:
        return max(0, (self.window_end - self.window_start).days)

    @property
    def truncated_from(self) -> datetime | None:
        return self.requested_start if self.truncated else None

    @property
    def truncation_note(self) -> str | None:
        if not self.truncated:
            return None
        return (
            "collection window truncated to max_lookback_days="
            f"{self.max_lookback_days}"
        )

    @property
    def truncation(self) -> dict[str, Any] | None:
        if not self.truncated:
            return None
        return {
            "truncated": True,
            "requested_start": self.requested_start.isoformat(),
            "actual_start": self.window_start.isoformat(),
            "max_lookback_days": self.max_lookback_days,
            "note": self.truncation_note,
        }

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "requested_start": self.requested_start.isoformat(),
            "lookback_days": self.lookback_days,
            "initial_lookback_days": self.initial_lookback_days,
            "max_lookback_days": self.max_lookback_days,
            "truncated": self.truncated,
        }
        if self.truncated:
            payload["truncated_from"] = self.requested_start.isoformat()
            payload["truncation_note"] = self.truncation_note
        return payload

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        if key == "truncated_from":
            return self.truncated_from
        if key == "truncation":
            return self.truncation
        return self.as_dict()[key]


def calculate_collection_window(
    *,
    cursor: Any = None,
    last_success_at: datetime | str | None = None,
    now: datetime | str | None = None,
    initial_lookback_days: int = DEFAULT_INITIAL_LOOKBACK_DAYS,
    max_lookback_days: int = DEFAULT_MAX_LOOKBACK_DAYS,
    recovery: bool = False,
) -> CollectionWindow:
    """Return a first-run or bounded recovery window.

    A missing cursor starts with 14 days by default.  A recovery attempt starts
    at the last successful window end; if that interval is older than 30 days,
    its beginning is moved forward and the truncation is retained in the result.
    """

    initial_days = _positive_days(initial_lookback_days, name="initial_lookback_days")
    max_days = _positive_days(max_lookback_days, name="max_lookback_days")
    if max_days == 0:
        raise ValueError("max_lookback_days must be positive")
    end = _as_datetime(now, name="now") if now is not None else datetime.now(UTC)

    previous_end = _cursor_datetime(cursor)
    if previous_end is None and last_success_at is not None:
        previous_end = _as_datetime(last_success_at, name="last_success_at")

    if previous_end is None:
        requested_start = end - timedelta(days=initial_days)
        bounded_start = requested_start
        truncated = False
        if recovery and requested_start < end - timedelta(days=max_days):
            bounded_start = end - timedelta(days=max_days)
            truncated = True
        return CollectionWindow(
            window_start=bounded_start,
            window_end=end,
            requested_start=requested_start,
            initial_lookback_days=initial_days,
            max_lookback_days=max_days,
            truncated=truncated,
        )

    requested_start = previous_end
    bounded_start = requested_start
    truncated = False
    if recovery or requested_start < end - timedelta(days=max_days):
        lower_bound = end - timedelta(days=max_days)
        if requested_start < lower_bound:
            bounded_start = lower_bound
            truncated = True

    return CollectionWindow(
        window_start=bounded_start,
        window_end=end,
        requested_start=requested_start,
        initial_lookback_days=initial_days,
        max_lookback_days=max_days,
        truncated=truncated,
    )


def build_collection_window(**kwargs: Any) -> CollectionWindow:
    """Compatibility spelling for :func:`calculate_collection_window`."""

    return calculate_collection_window(**kwargs)


def compute_collection_window(**kwargs: Any) -> CollectionWindow:
    """Compatibility spelling for :func:`calculate_collection_window`."""

    return calculate_collection_window(**kwargs)


def build_window(**kwargs: Any) -> CollectionWindow:
    """Short compatibility spelling for callers that only need a window."""

    return calculate_collection_window(**kwargs)


def calculate_window(**kwargs: Any) -> CollectionWindow:
    """Short compatibility spelling for callers that only need a window."""

    return calculate_collection_window(**kwargs)


__all__ = [
    "CollectionWindow",
    "DEFAULT_INITIAL_LOOKBACK_DAYS",
    "DEFAULT_MAX_LOOKBACK_DAYS",
    "build_collection_window",
    "build_window",
    "calculate_collection_window",
    "calculate_window",
    "compute_collection_window",
]
