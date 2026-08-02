"""Explicit §9.1 pipeline state machine.

State is deliberately kept separate from error/retry metadata.  A retry does
not create an invented state such as ``retrying``; the item remains in its
current state until a listed transition succeeds or the retry policy marks it
``failed``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Mapping, TypeVar


class PipelineState(StrEnum):
    DISCOVERED = "discovered"
    COLLECTED = "collected"
    NORMALIZED = "normalized"
    DEDUPLICATED = "deduplicated"
    CLASSIFIED = "classified"
    EXTRACTED = "extracted"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    DUPLICATE = "duplicate"
    IRRELEVANT = "irrelevant"
    BACKGROUND_ONLY = "background_only"
    ACCESS_DENIED = "access_denied"
    FAILED = "failed"


VALID_STATES = frozenset(item.value for item in PipelineState)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "discovered": frozenset({"collected", "access_denied", "failed"}),
    "collected": frozenset({"normalized", "failed"}),
    "normalized": frozenset({"deduplicated", "duplicate"}),
    "deduplicated": frozenset({"classified", "failed"}),
    "classified": frozenset({"extracted", "irrelevant", "background_only"}),
    "extracted": frozenset({"validated", "failed"}),
    "validated": frozenset({"needs_review", "rejected"}),
    "needs_review": frozenset({"approved", "rejected", "archived"}),
    "approved": frozenset(),
    "rejected": frozenset(),
    "archived": frozenset(),
    "duplicate": frozenset(),
    "irrelevant": frozenset(),
    "background_only": frozenset(),
    "access_denied": frozenset(),
    "failed": frozenset(),
}

# Compatibility name useful to callers that describe the table as a graph.
STATE_TRANSITIONS = ALLOWED_TRANSITIONS

TERMINAL_STATES = frozenset(
    {
        "approved",
        "rejected",
        "archived",
        "duplicate",
        "irrelevant",
        "background_only",
        "access_denied",
        "failed",
    }
)

ERROR_RETRY_FIELDS = (
    "error_class",
    "retry_count",
    "next_retry_at",
    "last_error_at",
    "terminal_error",
)


class InvalidTransition(ValueError):
    """Raised when a transition is not listed by §9.1."""


# A descriptive alias keeps integrations from depending on one exception name.
StateTransitionError = InvalidTransition


def _state_value(value: str | PipelineState) -> str:
    if isinstance(value, PipelineState):
        return value.value
    if value not in VALID_STATES:
        raise InvalidTransition(f"unknown pipeline state: {value!r}")
    return value


def can_transition(current: str | PipelineState, target: str | PipelineState) -> bool:
    """Return whether one exact §9.1 transition is allowed."""

    try:
        current_value = _state_value(current)
        target_value = _state_value(target)
    except InvalidTransition:
        return False
    return target_value in ALLOWED_TRANSITIONS[current_value]


def transition(
    current: TypeVar("StateValue", str, PipelineState),
    target: TypeVar("StateValue", str, PipelineState),
):
    """Validate and return ``target`` without adding implicit transitions."""

    current_value = _state_value(current)
    target_value = _state_value(target)
    if target_value not in ALLOWED_TRANSITIONS[current_value]:
        raise InvalidTransition(
            f"invalid pipeline transition: {current_value!r} -> {target_value!r}"
        )
    return target


def is_terminal(state: str | PipelineState) -> bool:
    return _state_value(state) in TERMINAL_STATES


@dataclass(frozen=True)
class RetryMetadata:
    """Retry/error axis, independent from :class:`PipelineState`."""

    error_class: str | None = None
    retry_count: int = 0
    next_retry_at: datetime | None = None
    last_error_at: datetime | None = None
    terminal_error: bool = False

    def __post_init__(self) -> None:
        if self.retry_count < 0:
            raise ValueError("retry_count must not be negative")
        if self.terminal_error and self.error_class is None:
            raise ValueError("terminal_error requires error_class")

    def as_dict(self) -> dict[str, object | None]:
        return {
            "error_class": self.error_class,
            "retry_count": self.retry_count,
            "next_retry_at": self.next_retry_at,
            "last_error_at": self.last_error_at,
            "terminal_error": self.terminal_error,
        }


@dataclass(frozen=True)
class PipelineStateRecord:
    """State plus separate retry metadata for persistence adapters."""

    state: PipelineState | str
    retry: RetryMetadata = RetryMetadata()

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", PipelineState(_state_value(self.state)))

    @property
    def status(self) -> str:
        return self.state.value

    @property
    def error_class(self) -> str | None:
        return self.retry.error_class

    @property
    def retry_count(self) -> int:
        return self.retry.retry_count

    @property
    def next_retry_at(self) -> datetime | None:
        return self.retry.next_retry_at

    @property
    def last_error_at(self) -> datetime | None:
        return self.retry.last_error_at

    @property
    def terminal_error(self) -> bool:
        return self.retry.terminal_error

    def transition_to(self, target: PipelineState | str) -> "PipelineStateRecord":
        transition(self.state, target)
        return replace(self, state=PipelineState(_state_value(target)))

    def with_retry(self, retry: RetryMetadata) -> "PipelineStateRecord":
        return replace(self, retry=retry)

    def as_dict(self) -> dict[str, object | None]:
        return {"status": self.status, **self.retry.as_dict()}


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ERROR_RETRY_FIELDS",
    "InvalidTransition",
    "PipelineState",
    "PipelineStateRecord",
    "RetryMetadata",
    "STATE_TRANSITIONS",
    "StateTransitionError",
    "TERMINAL_STATES",
    "VALID_STATES",
    "can_transition",
    "is_terminal",
    "transition",
]
