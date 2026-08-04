"""Execution cursor rules for scheduled briefing windows.

The cursor is deliberately derived from briefing *execution* status.  A
delivery or publication result must not decide whether the collection window
advances: only a successful execution does that.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any

from scalping_briefing.sources.window import (
    DEFAULT_INITIAL_LOOKBACK_DAYS,
    DEFAULT_MAX_LOOKBACK_DAYS,
    CollectionWindow,
    calculate_collection_window,
)


def _as_datetime(value: Any, *, name: str) -> datetime | None:
    """Coerce a run timestamp using the same timezone convention as windows."""

    if value is None:
        return None
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


def _value(run: Any, *names: str) -> Any:
    if isinstance(run, Mapping):
        for name in names:
            if name in run:
                return run[name]
        return None
    for name in names:
        value = getattr(run, name, None)
        if value is not None:
            return value
    return None


def _runs(previous_runs: Iterable[Any] | Mapping[str, Any] | None) -> list[Any]:
    if previous_runs is None:
        return []
    if isinstance(previous_runs, Mapping):
        return [previous_runs]
    return list(previous_runs)


def _success_run(previous_runs: Iterable[Any] | Mapping[str, Any] | None, scheduled_for: datetime) -> Any | None:
    """Return latest successful run not later than current schedule."""

    selected: tuple[datetime, int, Any] | None = None
    for index, run in enumerate(_runs(previous_runs)):
        status = _value(run, "run_status", "status")
        if status != "success":
            continue
        cursor = _value(run, "cursor")
        end_value = _value(run, "window_end")
        if end_value is None and isinstance(cursor, Mapping):
            end_value = _value(cursor, "window_end")
        if end_value is None:
            end_value = _value(run, "scheduled_for")
        end = _as_datetime(end_value, name="window_end")
        if end is None or end > scheduled_for:
            continue
        # Keep input order as a deterministic tie-breaker for retry records
        # sharing one scheduled occurrence.
        candidate = (end, index, run)
        if selected is None or candidate[:2] > selected[:2]:
            selected = candidate
    return selected[2] if selected is not None else None


def _previous_cursor(run: Any | None, previous_end: datetime | None) -> dict[str, Any] | None:
    if run is None:
        return None
    cursor = _value(run, "cursor")
    if isinstance(cursor, Mapping):
        return deepcopy(dict(cursor))
    if previous_end is None:
        return None
    return {"window_end": previous_end}


def _next_cursor(window: CollectionWindow) -> dict[str, Any]:
    cursor: dict[str, Any] = {
        "window_end": window.window_end,
        "window_start": window.window_start,
        "window_truncated": window.truncated,
    }
    if window.truncated:
        # Keep both meanings explicit: ``truncated_from`` is the requested
        # (old) start, while ``truncated_start`` is the actual bounded start.
        cursor["truncated_from"] = window.requested_start
        cursor["truncated_start"] = window.window_start
        cursor["truncation"] = deepcopy(window.truncation)
    return cursor


class CursorAdvance(dict[str, Any]):
    """Mapping result with datetime fields and the source window attached.

    Mapping access is convenient for pipeline payloads; attributes preserve
    the same shape as :class:`CollectionWindow` for Python callers.
    """

    def __init__(
        self,
        window: CollectionWindow,
        *,
        cursor: Mapping[str, Any] | None,
        advanced: bool,
        run_status: str,
    ) -> None:
        self.window = window
        self.cursor = deepcopy(dict(cursor)) if cursor is not None else None
        self.advanced = advanced
        self.run_status = run_status
        super().__init__(
            scheduled_for=window.window_end,
            run_status=run_status,
            window_start=window.window_start,
            window_end=window.window_end,
            window_truncated=window.truncated,
            truncated_start=window.window_start if window.truncated else None,
            cursor=self.cursor,
        )

    @property
    def window_start(self) -> datetime:
        return self.window.window_start

    @property
    def window_end(self) -> datetime:
        return self.window.window_end

    @property
    def requested_start(self) -> datetime:
        return self.window.requested_start

    @property
    def window_truncated(self) -> bool:
        return self.window.truncated

    @property
    def truncated(self) -> bool:
        return self.window.truncated

    @property
    def truncated_start(self) -> datetime | None:
        return self.window.window_start if self.window.truncated else None

    @property
    def truncated_from(self) -> datetime | None:
        return self.window.truncated_from

    @property
    def truncation(self) -> dict[str, Any] | None:
        return self.window.truncation

    def __getitem__(self, key: str) -> Any:
        # Match CollectionWindow's compatibility mapping for callers that
        # inspect truncation metadata without switching to attributes.
        if key == "requested_start":
            return self.requested_start
        if key == "truncated_from":
            return self.truncated_from
        if key == "truncation":
            return self.truncation
        return super().__getitem__(key)

    def as_dict(self) -> dict[str, Any]:
        payload = self.window.as_dict()
        payload["window_truncated"] = self.window.truncated
        payload["truncated_start"] = (
            self.window.window_start.isoformat() if self.window.truncated else None
        )
        payload["cursor"] = _serialise(self.cursor)
        payload["advanced"] = self.advanced
        return payload

    to_dict = as_dict


def _serialise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _serialise(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialise(item) for item in value]
    return value


def advance_cursor(
    previous_runs: Iterable[Any] | Mapping[str, Any] | None,
    *,
    scheduled_for: datetime | date | str,
    run_status: str,
    initial_lookback_days: int = DEFAULT_INITIAL_LOOKBACK_DAYS,
    max_lookback_days: int = DEFAULT_MAX_LOOKBACK_DAYS,
) -> CursorAdvance:
    """Calculate the current execution window and success-only cursor update.

    ``previous_runs`` may contain mappings or ORM-like objects.  The latest
    preceding ``run_status == 'success'`` supplies the cursor.  Failed or
    otherwise incomplete executions leave that cursor unchanged, while their
    calculated window remains available to persist with the failed run.

    Window bounds and truncation come from
    :func:`scalping_briefing.sources.window.calculate_collection_window`; this
    function does not duplicate its bounded-window calculation.
    """

    scheduled = _as_datetime(scheduled_for, name="scheduled_for")
    if scheduled is None:
        raise ValueError("scheduled_for is required")

    prior = _success_run(previous_runs, scheduled)
    prior_cursor = _value(prior, "cursor") if prior is not None else None
    prior_end_value = _value(prior, "window_end") if prior is not None else None
    if prior_end_value is None and isinstance(prior_cursor, Mapping):
        prior_end_value = _value(prior_cursor, "window_end")
    if prior_end_value is None and prior is not None:
        prior_end_value = _value(prior, "scheduled_for")
    prior_end = _as_datetime(prior_end_value, name="window_end")

    window = calculate_collection_window(
        cursor=prior_end,
        now=scheduled,
        initial_lookback_days=initial_lookback_days,
        max_lookback_days=max_lookback_days,
        recovery=True,
    )
    advanced = run_status == "success"
    cursor = _next_cursor(window) if advanced else _previous_cursor(prior, prior_end)
    return CursorAdvance(
        window,
        cursor=cursor,
        advanced=advanced,
        run_status=run_status,
    )


__all__ = [
    "CursorAdvance",
    "DEFAULT_INITIAL_LOOKBACK_DAYS",
    "DEFAULT_MAX_LOOKBACK_DAYS",
    "advance_cursor",
]
