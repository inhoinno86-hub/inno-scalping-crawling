"""Route validated strategy candidates through the existing state machine.

Routing is deliberately a small policy boundary.  It reads the value score
and extraction fields already present on a candidate, applies P15, and makes
one explicitly validated ``validated`` transition.  It does not score,
validate, persist ORM rows, or introduce another state graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any

from scalping_briefing.config import load_config
from scalping_briefing.pipeline import state_machine


# P15 applies to every core field.  Keep this tuple local so routing remains a
# small policy boundary and does not create a dependency on validation result
# implementation details.
CORE_CONFLICT_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)
REVIEW_REASONS = (
    "borderline_score",
    "low_extraction_confidence",
    "conflicting_core_field",
)

_MISSING = object()


class RoutingError(ValueError):
    """Raised when a candidate cannot be safely routed."""


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _state(value: Any) -> str:
    for name in ("processing_status", "state", "status"):
        current = _field(value, name)
        if current is not _MISSING and current is not None:
            return str(getattr(current, "value", current))
    raise RoutingError("a validated candidate requires a document version state")


def _set_state(value: Any, target: str) -> None:
    current = _state(value)
    if current == target:
        return

    # The existing state machine is the sole authority for this transition.
    state_machine.transition(current, target)
    if isinstance(value, Mapping):
        try:
            value["processing_status"] = target  # type: ignore[index]
        except TypeError as exc:
            raise RoutingError("document version mapping must be mutable") from exc
    else:
        try:
            setattr(value, "processing_status", target)
        except (AttributeError, TypeError) as exc:
            raise RoutingError("document version state must be mutable") from exc


def _set_field(value: Any, name: str, target: Any) -> None:
    if isinstance(value, Mapping):
        try:
            value[name] = target  # type: ignore[index]
        except TypeError as exc:
            raise RoutingError("candidate mapping must be mutable") from exc
        return
    try:
        setattr(value, name, target)
    except (AttributeError, TypeError) as exc:
        raise RoutingError("candidate must be mutable") from exc


def _number(value: Any) -> float | None:
    if value is _MISSING or value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _setting(settings: Any, name: str) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name, _MISSING)
    return getattr(settings, name, _MISSING)


def _status_is_conflicting(candidate: Any, field_name: str) -> bool:
    direct = _field(candidate, f"{field_name}_status")
    statuses = _field(candidate, "field_status", {})
    mapped = (
        statuses.get(field_name, _MISSING)
        if isinstance(statuses, Mapping)
        else _MISSING
    )
    return any(
        isinstance(value, str) and value.strip().casefold() == "conflicting"
        for value in (direct, mapped)
    )


def _conflicting_fields(candidate: Any) -> tuple[str, ...]:
    return tuple(
        field_name
        for field_name in CORE_CONFLICT_FIELDS
        if _status_is_conflicting(candidate, field_name)
    )


def _unpack_candidate(
    candidate: Any,
    document_version: Any,
) -> tuple[Any, Any, Any]:
    """Accept a candidate, scoring result, or validation result."""

    source = candidate
    nested_candidate = _field(source, "candidate")
    if nested_candidate is not _MISSING and nested_candidate is not None:
        if document_version is None:
            nested_version = _field(source, "document_version")
            if nested_version is not _MISSING:
                document_version = nested_version
        candidate = nested_candidate

    # Also accept the state-first shape used by the validation boundary:
    # route_candidate(document_version, candidate).
    if document_version is not None:
        try:
            first_state = _state(source)
        except RoutingError:
            first_state = None
        try:
            second_state = _state(document_version)
        except RoutingError:
            second_state = None
        if first_state is not None and second_state is None:
            candidate, document_version = document_version, source

    return candidate, document_version, source


@dataclass(slots=True)
class RoutingResult:
    """Decision and state transition produced by :func:`route_candidate`."""

    candidate: Any
    document_version: Any
    processing_status: str
    reasons: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    value_score: float | None = None
    extraction_confidence: float | None = None
    candidate_score_threshold: float | None = None
    extraction_confidence_min: float | None = None

    @property
    def state(self) -> str:
        return self.processing_status

    @property
    def status(self) -> str:
        return self.processing_status

    @property
    def target(self) -> str:
        return self.processing_status

    @property
    def target_state(self) -> str:
        return self.processing_status

    @property
    def route(self) -> str:
        return self.processing_status

    @property
    def decision(self) -> str:
        return self.processing_status

    @property
    def forced_review(self) -> bool:
        return self.needs_review

    @property
    def review_status(self) -> str:
        return self.processing_status

    @property
    def needs_review(self) -> bool:
        return self.processing_status == "needs_review"

    @property
    def rejected(self) -> bool:
        return self.processing_status == "rejected"

    @property
    def review_reasons(self) -> tuple[str, ...]:
        return self.reasons

    def __getitem__(self, key: str) -> Any:
        values = self.as_dict()
        if key in values:
            return values[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def as_dict(self) -> dict[str, Any]:
        candidate_id = _field(self.candidate, "candidate_id", None)
        return {
            "candidate_id": None if candidate_id is _MISSING else candidate_id,
            "processing_status": self.processing_status,
            "review_status": self.processing_status,
            "reasons": list(self.reasons),
            "conflicting_fields": list(self.conflicting_fields),
            "value_score": self.value_score,
            "extraction_confidence": self.extraction_confidence,
        }


def route_candidate(
    candidate: Any,
    document_version: Any = None,
    *,
    settings: Any = None,
    config: Any = None,
    value_score: int | float | None = None,
    score: int | float | None = None,
    extraction_confidence: int | float | None = None,
    candidate_score_threshold: int | float | None = None,
    extraction_confidence_min: int | float | None = None,
) -> RoutingResult:
    """Route a validated candidate to ``needs_review`` or ``rejected``.

    P15 is fail-closed: a missing or malformed score/confidence cannot prove
    that a candidate is safe to reject, so it is sent to review.  A score at
    either inclusive ``candidate_score_threshold ± 10`` boundary is also
    sent to review.
    """

    if settings is not None and config is not None:
        raise TypeError("provide settings or config, not both")
    if value_score is not None and score is not None:
        raise TypeError("provide value_score or score, not both")

    candidate, document_version, source = _unpack_candidate(
        candidate,
        document_version,
    )
    if document_version is None:
        try:
            _state(candidate)
        except RoutingError as exc:
            raise RoutingError("document_version is required") from exc
        document_version = candidate

    selected_settings = settings if settings is not None else config
    if selected_settings is None:
        selected_settings = load_config()

    threshold_value = (
        candidate_score_threshold
        if candidate_score_threshold is not None
        else _setting(selected_settings, "candidate_score_threshold")
    )
    confidence_min_value = (
        extraction_confidence_min
        if extraction_confidence_min is not None
        else _setting(selected_settings, "extraction_confidence_min")
    )
    threshold = _number(threshold_value)
    confidence_min = _number(confidence_min_value)
    if threshold is None:
        raise RoutingError("candidate_score_threshold must be a finite number")
    if confidence_min is None:
        raise RoutingError("extraction_confidence_min must be a finite number")

    selected_score = value_score if value_score is not None else score
    if selected_score is None:
        selected_score = _field(candidate, "value_score")
        if selected_score is _MISSING:
            selected_score = _field(source, "value_score")
        if selected_score is _MISSING:
            selected_score = _field(source, "score")
    score_number = _number(selected_score)

    selected_confidence = extraction_confidence
    if selected_confidence is None:
        selected_confidence = _field(candidate, "extraction_confidence")
        if selected_confidence is _MISSING:
            selected_confidence = _field(source, "extraction_confidence")
    confidence_number = _number(selected_confidence)

    reasons: list[str] = []
    if score_number is None or threshold - 10 <= score_number <= threshold + 10:
        reasons.append(
            "borderline_score" if score_number is not None else "missing_value_score"
        )
    if confidence_number is None or confidence_number < confidence_min:
        reasons.append("low_extraction_confidence")
    conflicting_fields = _conflicting_fields(candidate)
    if conflicting_fields:
        reasons.append("conflicting_core_field")

    target = "needs_review" if reasons else "rejected"
    _set_state(document_version, target)
    _set_field(candidate, "review_status", target)

    return RoutingResult(
        candidate=candidate,
        document_version=document_version,
        processing_status=target,
        reasons=tuple(reasons),
        conflicting_fields=conflicting_fields,
        value_score=score_number,
        extraction_confidence=confidence_number,
        candidate_score_threshold=threshold,
        extraction_confidence_min=confidence_min,
    )


def route_validated_candidate(
    document_version: Any,
    candidate: Any,
    **kwargs: Any,
) -> RoutingResult:
    """State-first compatibility entry point for validated candidates."""

    return route_candidate(candidate, document_version=document_version, **kwargs)


route = route_candidate
route_strategy_candidate = route_candidate
apply_routing = route_candidate
determine_route = route_candidate


__all__ = [
    "CORE_CONFLICT_FIELDS",
    "REVIEW_REASONS",
    "RoutingError",
    "RoutingResult",
    "apply_routing",
    "determine_route",
    "route",
    "route_candidate",
    "route_strategy_candidate",
    "route_validated_candidate",
]
