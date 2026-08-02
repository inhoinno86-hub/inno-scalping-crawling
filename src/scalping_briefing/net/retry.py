"""Bounded collection retries with durable terminal-failure evidence."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from scalping_briefing import alerts as alerts_boundary


RetryClock = Callable[[], datetime | float | int]
ResultT = TypeVar("ResultT")


def _now(clock: RetryClock | None, supplied: datetime | None = None) -> datetime:
    if supplied is not None:
        return supplied
    if clock is None:
        return datetime.now(timezone.utc)
    value = clock()
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raise TypeError("clock must return datetime or a numeric timestamp")


def _error_class(error: BaseException | str) -> str:
    if isinstance(error, str):
        return error or "collection_error"
    return type(error).__name__


def _error_message(error: BaseException | str) -> str:
    if isinstance(error, str):
        return error
    message = str(error)
    return message or type(error).__name__


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True, slots=True)
class RetryState:
    """Retry/error-axis fields persisted alongside a collection item."""

    error_class: str | None = None
    retry_count: int = 0
    next_retry_at: datetime | None = None
    last_error_at: datetime | None = None
    terminal_error: bool = False
    status: str = "pending"
    source_id: str | None = None
    error_message: str | None = None
    alert_path: Path | None = None

    def __post_init__(self) -> None:
        if self.retry_count < 0:
            raise ValueError("retry_count must not be negative")
        if self.terminal_error and self.error_class is None:
            raise ValueError("terminal_error requires error_class")
        if self.terminal_error and self.status != "failed":
            raise ValueError("terminal_error requires failed status")

    @property
    def failed(self) -> bool:
        return self.terminal_error

    @property
    def should_retry(self) -> bool:
        return not self.terminal_error

    @property
    def retry_scheduled(self) -> bool:
        return not self.terminal_error and self.next_retry_at is not None

    def as_dict(self) -> dict[str, object | None]:
        return {
            "error_class": self.error_class,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "last_error_at": self.last_error_at,
            "terminal_error": self.terminal_error,
            "status": self.status,
            "source_id": self.source_id,
            "error_message": self.error_message,
        }

    def as_json_dict(self) -> dict[str, object | None]:
        return {key: _json_value(value) for key, value in self.as_dict().items()}


def _emit_terminal_failure(
    state: RetryState,
    *,
    error: BaseException | str,
    logger: logging.Logger,
    alerts_dir: str | Path,
    alert_event: str,
) -> Path:
    """Write the structured log and local alert through the existing boundary."""

    fields = state.as_dict()
    log_fields = {
        key: value
        for key, value in fields.items()
        if key != "alert_path"
    }
    log_fields.update(
        {
            "event": alert_event,
            "error": _error_message(error),
            "terminal_error": True,
        }
    )
    log_exception: BaseException | None = None
    try:
        logger.log(logging.ERROR, alert_event, extra=log_fields)
    except Exception as exc:  # pragma: no cover - defensive logging boundary
        log_exception = exc

    alert_details = state.as_json_dict()
    alert_details.update(
        {
            "error": _error_message(error),
            "terminal_error": True,
        }
    )
    alert_path = alerts_boundary.record_failure(
        alert_event,
        _error_message(error),
        details=alert_details,
        alerts_dir=alerts_dir,
    )
    if log_exception is not None:  # the alert exists; surface observability loss
        raise RuntimeError("structured collection failure log could not be written") from log_exception
    return alert_path


class RetryPolicy:
    """Apply the configured retry cap and exponential schedule."""

    def __init__(
        self,
        max_collect_retries: int = 3,
        *,
        base_backoff_seconds: float = 1.0,
        backoff_base_seconds: float | None = None,
        max_backoff_seconds: float = 60.0,
        clock: RetryClock | None = None,
        logger: logging.Logger | None = None,
        alerts_dir: str | Path = "alerts/",
        alert_event: str = "collection_failure",
    ) -> None:
        if max_collect_retries < 0:
            raise ValueError("max_collect_retries must not be negative")
        if backoff_base_seconds is not None:
            if base_backoff_seconds != 1.0:
                raise TypeError("use base_backoff_seconds or backoff_base_seconds, not both")
            base_backoff_seconds = float(backoff_base_seconds)
        if base_backoff_seconds < 0:
            raise ValueError("base_backoff_seconds must not be negative")
        if max_backoff_seconds < 0:
            raise ValueError("max_backoff_seconds must not be negative")
        if not alert_event:
            raise ValueError("alert_event must be non-empty")
        self.max_collect_retries = int(max_collect_retries)
        self.base_backoff_seconds = float(base_backoff_seconds)
        self.max_backoff_seconds = float(max_backoff_seconds)
        self.clock = clock
        self.logger = logger or logging.getLogger("scalping_briefing.collection")
        self.alerts_dir = alerts_dir
        self.alert_event = alert_event

    def backoff_seconds(self, retry_count: int) -> float:
        """Return the delay after the given one-based retry count."""

        if retry_count < 1:
            raise ValueError("retry_count must be at least one")
        return min(
            self.max_backoff_seconds,
            self.base_backoff_seconds * (2 ** (retry_count - 1)),
        )

    exponential_backoff = backoff_seconds

    def record_failure(
        self,
        error: BaseException | str,
        *,
        source_id: str | None = None,
        state: RetryState | None = None,
        retry_count: int | None = None,
        error_at: datetime | None = None,
        emit_alert: bool = True,
    ) -> RetryState:
        """Record one failure and emit evidence only when the cap is reached."""

        if state is not None and state.terminal_error:
            return state
        prior_count = state.retry_count if state is not None else (retry_count or 0)
        if prior_count < 0:
            raise ValueError("retry_count must not be negative")
        count = prior_count + 1
        timestamp = _now(self.clock, error_at)
        terminal = count >= self.max_collect_retries
        next_retry_at = None
        if not terminal:
            next_retry_at = timestamp + timedelta(seconds=self.backoff_seconds(count))
        next_state = RetryState(
            error_class=_error_class(error),
            retry_count=count,
            next_retry_at=next_retry_at,
            last_error_at=timestamp,
            terminal_error=terminal,
            status="failed" if terminal else "retry_scheduled",
            source_id=source_id if source_id is not None else (state.source_id if state else None),
            error_message=_error_message(error),
        )
        # A terminal collection failure always crosses both observability
        # boundaries.  Keep the legacy keyword accepted, but do not let it
        # suppress the required evidence.
        if terminal:
            path = _emit_terminal_failure(
                next_state,
                error=error,
                logger=self.logger,
                alerts_dir=self.alerts_dir,
                alert_event=self.alert_event,
            )
            next_state = replace(next_state, alert_path=path)
        return next_state

    failure = record_failure
    on_failure = record_failure
    next_state = record_failure
    handle_failure = record_failure

    def run(
        self,
        operation: Callable[[], ResultT],
        *,
        source_id: str | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        on_retry: Callable[[RetryState], None] | None = None,
    ) -> "RetryRunResult[ResultT]":
        """Run an operation, allowing the initial try plus three retries by default."""

        state = RetryState(source_id=source_id)
        attempts = 0
        while True:
            attempts += 1
            try:
                value = operation()
            except Exception as error:
                state = self.record_failure(error, source_id=source_id, state=state)
                if state.terminal_error:
                    raise CollectionFailedError(error, state, attempts) from error
                if on_retry is not None:
                    on_retry(state)
                delay = self.backoff_seconds(state.retry_count)
                sleeper(delay)
                continue
            if state.retry_count:
                state = replace(state, status="success", next_retry_at=None)
            return RetryRunResult(value=value, state=state, attempts=attempts)

    execute = run


@dataclass(frozen=True, slots=True)
class RetryRunResult(Generic[ResultT]):
    value: ResultT
    state: RetryState
    attempts: int

    @property
    def succeeded(self) -> bool:
        return not self.state.terminal_error

    def __bool__(self) -> bool:
        return self.succeeded


class CollectionFailedError(RuntimeError):
    """Raised after the retry cap records a terminal collection failure."""

    def __init__(
        self,
        cause: BaseException | str,
        state: RetryState,
        attempts: int,
    ) -> None:
        self.cause = cause
        self.state = state
        self.attempts = attempts
        super().__init__(
            f"collection failed after {attempts} attempts: {state.error_class}: "
            f"{state.error_message}"
        )


def run_with_retries(
    operation: Callable[[], ResultT],
    *,
    source_id: str | None = None,
    policy: RetryPolicy | None = None,
    max_collect_retries: int = 3,
    base_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
    clock: RetryClock | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
    alerts_dir: str | Path = "alerts/",
    on_retry: Callable[[RetryState], None] | None = None,
) -> RetryRunResult[ResultT]:
    """Functional wrapper around :class:`RetryPolicy`."""

    selected = policy or RetryPolicy(
        max_collect_retries=max_collect_retries,
        base_backoff_seconds=base_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
        clock=clock,
        logger=logger,
        alerts_dir=alerts_dir,
    )
    return selected.run(
        operation,
        source_id=source_id,
        sleeper=sleeper,
        on_retry=on_retry,
    )


retry_with_backoff = run_with_retries
execute_with_retries = run_with_retries
run_collection_with_retries = run_with_retries
collect_with_retries = run_with_retries


def record_collection_failure(
    error: BaseException | str,
    *,
    source_id: str | None = None,
    retry_count: int = 3,
    last_error_at: datetime | None = None,
    logger: logging.Logger | None = None,
    alerts_dir: str | Path = "alerts/",
    alert_event: str = "collection_failure",
) -> Path:
    """Emit a pre-built terminal failure through both observability paths."""

    state = RetryState(
        error_class=_error_class(error),
        retry_count=retry_count,
        next_retry_at=None,
        last_error_at=last_error_at or datetime.now(timezone.utc),
        terminal_error=True,
        status="failed",
        source_id=source_id,
        error_message=_error_message(error),
    )
    return _emit_terminal_failure(
        state,
        error=error,
        logger=logger or logging.getLogger("scalping_briefing.collection"),
        alerts_dir=alerts_dir,
        alert_event=alert_event,
    )


record_failure = record_collection_failure
record_terminal_failure = record_collection_failure
RetryMetadata = RetryState
CollectionRetryState = RetryState
RetryRecord = RetryState
CollectionRetryPolicy = RetryPolicy
RetryController = RetryPolicy
RetryExhaustedError = CollectionFailedError
CollectionRetryExhausted = CollectionFailedError


__all__ = [
    "CollectionFailedError",
    "CollectionRetryExhausted",
    "CollectionRetryPolicy",
    "CollectionRetryState",
    "RetryExhaustedError",
    "RetryMetadata",
    "RetryPolicy",
    "RetryRecord",
    "RetryRunResult",
    "RetryState",
    "RetryController",
    "execute_with_retries",
    "collect_with_retries",
    "record_collection_failure",
    "record_failure",
    "record_terminal_failure",
    "retry_with_backoff",
    "run_collection_with_retries",
    "run_with_retries",
]
