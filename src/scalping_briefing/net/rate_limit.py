"""Deterministic, source-keyed request limiting.

The limiter owns only request timing.  It does not make transport decisions,
and state for one source can never consume the allowance of another source.
The clock is injectable so fixture tests can advance time without sleeping.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


Clock = Callable[[], datetime | float | int]


def _read_clock(clock: Clock) -> tuple[datetime | float, float]:
    value = clock()
    if isinstance(value, datetime):
        comparable = value
        if comparable.tzinfo is None:
            comparable = comparable.replace(tzinfo=timezone.utc)
        return value, comparable.timestamp()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value), float(value)
    raise TypeError("clock must return datetime or a numeric monotonic value")


def _add_seconds(value: datetime | float, seconds: float) -> datetime | float:
    if isinstance(value, datetime):
        return value + timedelta(seconds=seconds)
    return value + seconds


def _policy_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    """The numeric part of one source policy's ``rate_limit`` record."""

    requests_per_minute: float
    burst: int = 1
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if self.burst < 1:
            raise ValueError("burst must be at least one")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")

    @property
    def interval_seconds(self) -> float:
        """Return the minimum interval between replenished request tokens."""

        return 60.0 / float(self.requests_per_minute)

    @property
    def interval(self) -> float:
        return self.interval_seconds

    @classmethod
    def from_value(cls, value: Any) -> "RateLimitPolicy":
        """Read either a rate-limit mapping or a complete source mapping."""

        if isinstance(value, cls):
            return value
        nested = _policy_value(value, "rate_limit")
        if nested is not None:
            value = nested

        requests_per_minute = _policy_value(value, "requests_per_minute")
        if requests_per_minute is None:
            requests_per_minute = _policy_value(value, "requests_per_minute_limit")
        if requests_per_minute is None:
            interval = _policy_value(value, "interval_seconds")
            if interval is not None:
                interval_value = float(interval)
                if interval_value <= 0:
                    raise ValueError("interval_seconds must be positive")
                requests_per_minute = 60.0 / interval_value
        if requests_per_minute is None:
            retry_after = _policy_value(value, "retry_after_seconds")
            if retry_after is not None and float(retry_after) > 0:
                requests_per_minute = 60.0 / float(retry_after)
        if requests_per_minute is None:
            raise ValueError("rate limit policy requires requests_per_minute")

        burst = _policy_value(value, "burst", 1)
        retry_after = _policy_value(value, "retry_after_seconds")
        return cls(
            requests_per_minute=float(requests_per_minute),
            burst=int(burst),
            retry_after_seconds=(None if retry_after is None else float(retry_after)),
        )


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """One non-blocking limiter decision."""

    source_id: str
    allowed: bool
    wait_seconds: float
    next_allowed_at: datetime | float
    interval_seconds: float

    @property
    def should_wait(self) -> bool:
        return not self.allowed

    @property
    def retry_after_seconds(self) -> float:
        return self.wait_seconds

    @property
    def wait(self) -> float:
        return self.wait_seconds

    @property
    def next_allowed(self) -> datetime | float:
        return self.next_allowed_at

    def __bool__(self) -> bool:
        return self.allowed

    def as_dict(self) -> dict[str, object]:
        next_allowed_at: object = self.next_allowed_at
        if isinstance(next_allowed_at, datetime):
            next_allowed_at = next_allowed_at.isoformat()
        return {
            "source_id": self.source_id,
            "allowed": self.allowed,
            "wait_seconds": self.wait_seconds,
            "next_allowed_at": next_allowed_at,
            "interval_seconds": self.interval_seconds,
        }


@dataclass(slots=True)
class _TokenBucket:
    tokens: float
    last_timestamp: float


class SourceRateLimiter:
    """Enforce an independent token bucket for every source key.

    ``acquire`` is deliberately non-blocking: a denied decision contains the
    exact delay required before the next request.  ``acquire_or_wait`` is
    available for callers that explicitly want to sleep.
    """

    def __init__(
        self,
        policies: Mapping[str, Any] | None = None,
        *,
        source_policies: Mapping[str, Any] | None = None,
        policy: Any | None = None,
        clock: Clock | None = None,
        time_fn: Clock | None = None,
    ) -> None:
        if policies is not None and source_policies is not None:
            raise TypeError("use policies or source_policies, not both")
        if clock is not None and time_fn is not None:
            raise TypeError("use clock or time_fn, not both")
        self._policies = dict(policies or source_policies or {})
        self._default_policy = policy
        self._clock: Clock = (
            clock if clock is not None else time_fn if time_fn is not None else time.monotonic
        )
        self._buckets: dict[str, _TokenBucket] = {}

    def _policy_for(self, source_id: str, policy: Any | None) -> RateLimitPolicy:
        selected = policy
        if selected is None:
            selected = self._policies.get(source_id, self._default_policy)
        if selected is None:
            raise ValueError(f"no rate limit policy configured for source {source_id!r}")
        return RateLimitPolicy.from_value(selected)

    def _decision(
        self,
        source_id: str,
        selected: RateLimitPolicy,
        *,
        consume: bool,
    ) -> RateLimitDecision:
        now, timestamp = _read_clock(self._clock)
        bucket = self._buckets.get(source_id)
        if bucket is None:
            bucket = _TokenBucket(
                tokens=float(selected.burst),
                last_timestamp=timestamp,
            )
            if consume:
                bucket.tokens -= 1.0
                self._buckets[source_id] = bucket
            return RateLimitDecision(
                source_id=source_id,
                allowed=True,
                wait_seconds=0.0,
                next_allowed_at=now,
                interval_seconds=selected.interval_seconds,
            )

        elapsed = max(0.0, timestamp - bucket.last_timestamp)
        tokens = min(
            float(selected.burst),
            bucket.tokens + elapsed / selected.interval_seconds,
        )
        if tokens >= 1.0:
            decision = RateLimitDecision(
                source_id=source_id,
                allowed=True,
                wait_seconds=0.0,
                next_allowed_at=now,
                interval_seconds=selected.interval_seconds,
            )
            if consume:
                bucket.tokens = tokens - 1.0
                bucket.last_timestamp = timestamp
            return decision

        wait_seconds = max(0.0, (1.0 - tokens) * selected.interval_seconds)
        decision = RateLimitDecision(
            source_id=source_id,
            allowed=False,
            wait_seconds=wait_seconds,
            next_allowed_at=_add_seconds(now, wait_seconds),
            interval_seconds=selected.interval_seconds,
        )
        if consume:
            bucket.tokens = tokens
            bucket.last_timestamp = timestamp
        return decision

    def acquire(self, source_id: str, policy: Any | None = None) -> RateLimitDecision:
        """Consume one token or return the deterministic required wait."""

        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        selected = self._policy_for(source_id, policy)
        return self._decision(source_id, selected, consume=True)

    def check(self, source_id: str, policy: Any | None = None) -> RateLimitDecision:
        """Inspect allowance without consuming a token."""

        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("source_id must be a non-empty string")
        selected = self._policy_for(source_id, policy)
        return self._decision(source_id, selected, consume=False)

    can_acquire = check
    enforce = acquire
    allow = acquire

    def wait_for(self, source_id: str, policy: Any | None = None) -> float:
        """Return the current wait in seconds without consuming allowance."""

        return self.check(source_id, policy).wait_seconds

    time_until_allowed = wait_for
    wait = wait_for

    def acquire_or_wait(
        self,
        source_id: str,
        policy: Any | None = None,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        max_waits: int = 100,
    ) -> RateLimitDecision:
        """Sleep until an allowance is available, using injected dependencies."""

        if max_waits < 1:
            raise ValueError("max_waits must be positive")
        for _ in range(max_waits):
            decision = self.acquire(source_id, policy)
            if decision.allowed:
                return decision
            sleeper(decision.wait_seconds)
        raise TimeoutError("rate limiter did not become available within max_waits")

    def reset(self, source_id: str | None = None) -> None:
        """Clear one source bucket or all limiter state."""

        if source_id is None:
            self._buckets.clear()
        else:
            self._buckets.pop(source_id, None)


RateLimiter = SourceRateLimiter
PerSourceRateLimiter = SourceRateLimiter
TokenBucketRateLimiter = SourceRateLimiter
RateLimit = RateLimitPolicy
RateLimitConfig = RateLimitPolicy

RateLimitPolicy.from_source = RateLimitPolicy.from_value  # type: ignore[attr-defined]


def rate_limit_interval(policy: Any) -> float:
    """Return the enforced interval for a source or rate-limit mapping."""

    return RateLimitPolicy.from_value(policy).interval_seconds


get_rate_limit_interval = rate_limit_interval


__all__ = [
    "PerSourceRateLimiter",
    "RateLimit",
    "RateLimitDecision",
    "RateLimitConfig",
    "RateLimitPolicy",
    "RateLimiter",
    "SourceRateLimiter",
    "TokenBucketRateLimiter",
    "get_rate_limit_interval",
    "rate_limit_interval",
]
