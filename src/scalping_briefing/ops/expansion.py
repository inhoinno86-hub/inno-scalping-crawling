"""Four-week expansion gates and recommendation-only operating advice.

The expansion boundary is deliberately read-only.  It consumes already
calculated weekly metric results, checks the most recent four windows, and
returns structured recommendations.  It never changes configuration, source
policy records, publication policy, or any other persisted state.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any


REQUIRED_WINDOW_COUNT = 4
REQUIRED_METRIC_IDS = ("M1", "M2", "M3", "M4", "M5", "M6")

REASON_MEETS_TARGET = "meets_target"
REASON_BREACHED = "breached"
REASON_INSUFFICIENT_DATA = "insufficient_data"

RECOMMEND = "recommend"
HOLD = "hold"

EXPANSION_CANDIDATES = (
    "auto_publish",
    "real_source_activation",
    "search_ui",
)

# These are the only Appendix A values whose change condition is Phase 4
# measurement.  Keep this mapping local and immutable by convention: it is a
# proposal catalogue, not a second configuration source.
APPENDIX_A_RECALIBRATION_DEFAULTS: dict[str, int | float] = {
    "initial_lookback_days": 14,
    "max_lookback_days": 30,
    "candidate_score_threshold": 60,
    "briefing_max_items": 7,
    "extraction_confidence_min": 0.7,
    "max_collect_retries": 3,
}
APPENDIX_A_RECALIBRATION_KEYS = tuple(APPENDIX_A_RECALIBRATION_DEFAULTS)

# Compatibility names for callers that use the Appendix A terminology.
APPENDIX_A_VALUES = APPENDIX_A_RECALIBRATION_DEFAULTS
APPENDIX_A_DEFAULTS = APPENDIX_A_RECALIBRATION_DEFAULTS
RECALIBRATION_KEYS = APPENDIX_A_RECALIBRATION_KEYS


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read the first present field from mappings and record-like values."""

    for name in names:
        if isinstance(value, Mapping):
            if name in value:
                return value[name]
            continue
        try:
            found = getattr(value, name)
        except (AttributeError, KeyError):
            continue
        if found is not None:
            return found
    return default


def _clean_id(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _normalise_metric_id(value: Any) -> str:
    return str(value).strip().upper() if value is not None else ""


def _normalise_verdict(metric: Any) -> str:
    """Return a closed verdict set, failing closed for incomplete records."""

    if isinstance(metric, str):
        verdict = metric.strip().lower()
        if verdict in {
            REASON_MEETS_TARGET,
            REASON_BREACHED,
            REASON_INSUFFICIENT_DATA,
        }:
            return verdict

    raw = _field(metric, "verdict", "status", "result")
    if raw is not None:
        verdict = str(raw).strip().lower()
        if verdict in {
            REASON_MEETS_TARGET,
            REASON_BREACHED,
            REASON_INSUFFICIENT_DATA,
        }:
            return verdict

    if _field(metric, "insufficient_data", "is_insufficient") is True:
        return REASON_INSUFFICIENT_DATA

    sample_size = _field(metric, "sample_size", "sample")
    value = _field(metric, "value")
    if value is None or sample_size == 0:
        return REASON_INSUFFICIENT_DATA

    meets = _field(metric, "meets_target", "target_met", "meets")
    if isinstance(meets, bool):
        return REASON_MEETS_TARGET if meets else REASON_BREACHED
    return REASON_INSUFFICIENT_DATA


def _metric_records(metrics: Any) -> dict[str, Any]:
    """Normalize list, mapping, or single metric results by metric ID."""

    if metrics is None:
        return {}
    if isinstance(metrics, Mapping):
        # A single MetricResult-like mapping has its ID in the record itself.
        if _field(metrics, "metric_id", "id") is not None:
            values: list[tuple[Any, Any]] = [(None, metrics)]
        else:
            values = list(metrics.items())
    elif isinstance(metrics, (str, bytes, bytearray)):
        return {}
    else:
        try:
            values = [(None, value) for value in metrics]
        except TypeError:
            values = [(None, metrics)]

    records: dict[str, Any] = {}
    for supplied_id, metric in values:
        metric_id = _field(metric, "metric_id", "id", default=supplied_id)
        identifier = _normalise_metric_id(metric_id)
        if identifier:
            records[identifier] = metric
    return records


def _looks_like_observation(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return any(
            _field(value, name) is not None
            for name in ("metrics", "metric_results", "results", "window", "window_id")
        )
    return bool(
        any(
            name in value
            for name in (
                "window",
                "observation_window",
                "period",
                "window_id",
                "window_start",
                "window_end",
                "metrics",
                "metric_results",
                "results",
                "metric_values",
            )
        )
        or any(_normalise_metric_id(key) in REQUIRED_METRIC_IDS for key in value)
    )


@dataclass(frozen=True, slots=True)
class _Observation:
    window: Any
    metrics: Any
    ordinal: int


def _iter_observations(value: Any) -> list[_Observation]:
    """Accept the public collection shapes without changing their records."""

    if value is None:
        return []

    if isinstance(value, Mapping):
        for wrapper_name in ("observations", "windows", "window_results"):
            wrapped = value.get(wrapper_name)
            if wrapped is not None:
                return _iter_observations(wrapped)
        if _looks_like_observation(value):
            return [_Observation(value, _field(value, "metrics", "metric_results", "results", "metric_values"), 0)]

        # A mapping keyed by window ID is a useful compact input form.
        observations: list[_Observation] = []
        for ordinal, (window_id, metrics) in enumerate(value.items()):
            observations.append(
                _Observation(
                    {"window_id": window_id},
                    metrics,
                    ordinal,
                )
            )
        return observations

    if isinstance(value, (str, bytes, bytearray)):
        return []

    try:
        items = list(value)
    except TypeError:
        items = [value]

    observations = []
    for ordinal, item in enumerate(items):
        if isinstance(item, tuple) and len(item) >= 2:
            observations.append(_Observation(item[0], item[1], ordinal))
            continue
        if isinstance(item, list) and len(item) == 2:
            observations.append(_Observation(item[0], item[1], ordinal))
            continue
        metrics = _field(item, "metrics", "metric_results", "results", "metric_values")
        window = _field(item, "window", "observation_window", "period")
        if window is None and (
            _field(item, "window_id", "window_start", "window_end", "start", "end") is not None
        ):
            window = item
        observations.append(_Observation(window, metrics, ordinal))
    return observations


def _window_info(observation: _Observation) -> dict[str, Any]:
    window = observation.window
    if window is None:
        return {
            "window_id": f"window-{observation.ordinal + 1}",
            "start": None,
            "end": None,
        }

    identifier = _field(window, "window_id", "id", "name", "label")
    if identifier is None and isinstance(window, (str, int, float)):
        identifier = window
    start = _field(window, "start", "window_start", "actual_start")
    end = _field(window, "end", "window_end", "actual_end")
    return {
        "window_id": _clean_id(identifier, f"window-{observation.ordinal + 1}"),
        "start": start,
        "end": end,
    }


def _time_value(value: Any) -> tuple[int, Any] | None:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.timestamp()
        else:
            value = value.isoformat()
        return (0, value)
    if isinstance(value, date):
        return (1, value.toordinal())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (2, value)
    if isinstance(value, str) and value.strip():
        return (3, value.strip())
    return None


def _sort_observations(observations: list[_Observation]) -> list[tuple[_Observation, dict[str, Any]]]:
    prepared = [(observation, _window_info(observation)) for observation in observations]

    def key(pair: tuple[_Observation, dict[str, Any]]) -> tuple[int, Any, int]:
        observation, info = pair
        value = _time_value(info["end"]) or _time_value(info["start"])
        if value is None:
            return (4, observation.ordinal, observation.ordinal)
        # A mixed set of timestamp types is ordered by its stable string form.
        if value[0] == 0 and isinstance(value[1], str):
            return (value[0], value[1], observation.ordinal)
        return (value[0], value[1], observation.ordinal)

    # Stable sort preserves caller order when windows have no temporal fields.
    return sorted(prepared, key=key)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _consecutive(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Check continuity when timestamps are available; opaque IDs stay usable."""

    previous_end = _as_datetime(previous.get("end"))
    current_start = _as_datetime(current.get("start"))
    if previous_end is not None and current_start is not None:
        if previous_end.tzinfo is not None and current_start.tzinfo is None:
            current_start = current_start.replace(tzinfo=previous_end.tzinfo)
        elif current_start.tzinfo is not None and previous_end.tzinfo is None:
            previous_end = previous_end.replace(tzinfo=current_start.tzinfo)
        return current_start >= previous_end and current_start - previous_end <= timedelta(days=1)

    previous_start = _as_datetime(previous.get("start"))
    current_start = _as_datetime(current.get("start"))
    if previous_start is not None and current_start is not None:
        if previous_start.tzinfo is not None and current_start.tzinfo is None:
            current_start = current_start.replace(tzinfo=previous_start.tzinfo)
        elif current_start.tzinfo is not None and previous_start.tzinfo is None:
            previous_start = previous_start.replace(tzinfo=current_start.tzinfo)
        return current_start - previous_start == timedelta(days=7)
    return True


@dataclass(frozen=True, slots=True)
class ExpansionBlocker(Mapping[str, Any]):
    """One window/metric pair that prevents expansion."""

    window_id: str
    metric_id: str
    reason: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {
            "window_id": self.window_id,
            "metric_id": self.metric_id,
            "reason": self.reason,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


@dataclass(frozen=True, slots=True)
class ExpansionDecision(Mapping[str, Any]):
    """Result of the four-window gate, with explicit blocking evidence."""

    expansion_eligible: bool
    reason: str
    windows_evaluated: int
    window_ids: tuple[str, ...] = ()
    blocked_windows: tuple[str, ...] = ()
    blocked_metrics: tuple[ExpansionBlocker, ...] = ()
    required_windows: int = REQUIRED_WINDOW_COUNT
    required_metrics: tuple[str, ...] = REQUIRED_METRIC_IDS
    recommendations: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    recalibration: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def eligible(self) -> bool:
        return self.expansion_eligible

    @property
    def blockers(self) -> tuple[ExpansionBlocker, ...]:
        return self.blocked_metrics

    @property
    def blocked_metric_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(blocker.metric_id for blocker in self.blocked_metrics))

    @property
    def insufficient_data(self) -> tuple[ExpansionBlocker, ...]:
        return tuple(
            blocker
            for blocker in self.blocked_metrics
            if blocker.reason == REASON_INSUFFICIENT_DATA
        )

    @property
    def breached(self) -> tuple[ExpansionBlocker, ...]:
        return tuple(
            blocker for blocker in self.blocked_metrics if blocker.reason == REASON_BREACHED
        )

    def as_dict(self) -> dict[str, Any]:
        blockers = [blocker.as_dict() for blocker in self.blocked_metrics]
        insufficient = [
            blocker for blocker in blockers if blocker["reason"] == REASON_INSUFFICIENT_DATA
        ]
        breached = [blocker for blocker in blockers if blocker["reason"] == REASON_BREACHED]
        return {
            "expansion_eligible": self.expansion_eligible,
            "eligible": self.expansion_eligible,
            "reason": self.reason,
            "windows_evaluated": self.windows_evaluated,
            "window_count": self.windows_evaluated,
            "required_windows": self.required_windows,
            "required_metrics": self.required_metrics,
            "window_ids": self.window_ids,
            "blocked_windows": self.blocked_windows,
            "blocked_metrics": blockers,
            "blocked_metric_ids": self.blocked_metric_ids,
            "blockers": blockers,
            "insufficient_data": insufficient,
            "breached": breached,
            "recommendations": dict(self.recommendations),
            "expansion_recommendations": dict(self.recommendations),
            "recalibration": dict(self.recalibration),
            "threshold_recommendations": dict(self.recalibration),
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return len(self.as_dict())


def evaluate_four_week_expansion(
    observations: Any = None,
    *,
    windows: Any = None,
    window_results: Any = None,
    observations_by_window: Any = None,
    required_window_count: int = REQUIRED_WINDOW_COUNT,
    required_metric_ids: Sequence[str] = REQUIRED_METRIC_IDS,
) -> ExpansionDecision:
    """Evaluate the latest consecutive weekly windows.

    ``observations`` can be a sequence of ``(window, metrics)`` pairs, a
    sequence of mappings with ``window``/``metrics`` fields, or a mapping from
    window IDs to metric results.  The latest ``required_window_count`` are
    selected by window end/start when temporal fields exist; otherwise input
    order is used.
    """

    if observations is None:
        observations = (
            windows
            if windows is not None
            else window_results
            if window_results is not None
            else observations_by_window
        )
    if not isinstance(required_window_count, int) or required_window_count <= 0:
        raise ValueError("required_window_count must be a positive integer")
    metric_ids = tuple(_normalise_metric_id(metric_id) for metric_id in required_metric_ids)
    if not metric_ids:
        raise ValueError("required_metric_ids must not be empty")

    ordered = _sort_observations(_iter_observations(observations))
    selected = ordered[-required_window_count:]
    selected_infos = [info for _, info in selected]
    blockers: list[ExpansionBlocker] = []

    missing_windows = max(0, required_window_count - len(selected))
    if missing_windows:
        for number in range(missing_windows):
            missing_id = f"missing-window-{number + 1}"
            for metric_id in metric_ids:
                blockers.append(
                    ExpansionBlocker(
                        missing_id,
                        metric_id,
                        REASON_INSUFFICIENT_DATA,
                        "weekly observation window is missing",
                    )
                )

    for previous, current in zip(selected_infos, selected_infos[1:]):
        if not _consecutive(previous, current):
            blockers.append(
                ExpansionBlocker(
                    current["window_id"],
                    "WINDOW_CONTINUITY",
                    REASON_INSUFFICIENT_DATA,
                    "selected windows are not consecutive weekly windows",
                )
            )

    for observation, info in selected:
        records = _metric_records(
            observation.metrics
            if observation.metrics is not None
            else _field(observation.window, "metrics", "metric_results", "results")
        )
        for metric_id in metric_ids:
            metric = records.get(metric_id)
            if metric is None:
                blockers.append(
                    ExpansionBlocker(
                        info["window_id"],
                        metric_id,
                        REASON_INSUFFICIENT_DATA,
                        "metric is missing from weekly observation",
                    )
                )
                continue
            verdict = _normalise_verdict(metric)
            if verdict != REASON_MEETS_TARGET:
                blockers.append(
                    ExpansionBlocker(
                        info["window_id"],
                        metric_id,
                        verdict,
                        "metric does not meet target" if verdict == REASON_BREACHED else "metric data is insufficient",
                    )
                )

    blocked_window_ids = tuple(dict.fromkeys(blocker.window_id for blocker in blockers))
    selected_window_ids = tuple(info["window_id"] for info in selected_infos)
    has_insufficient = any(
        blocker.reason == REASON_INSUFFICIENT_DATA for blocker in blockers
    )
    has_breach = any(blocker.reason == REASON_BREACHED for blocker in blockers)
    if has_insufficient:
        reason = REASON_INSUFFICIENT_DATA
    elif has_breach:
        reason = REASON_BREACHED
    else:
        reason = REASON_MEETS_TARGET

    return ExpansionDecision(
        expansion_eligible=not blockers and len(selected) == required_window_count,
        reason=reason,
        windows_evaluated=len(selected),
        window_ids=selected_window_ids,
        blocked_windows=blocked_window_ids,
        blocked_metrics=tuple(blockers),
        required_windows=required_window_count,
        required_metrics=metric_ids,
    )


def _coerce_decision(value: Any) -> ExpansionDecision:
    if isinstance(value, ExpansionDecision):
        return value
    if isinstance(value, Mapping) and _field(value, "expansion_eligible", "eligible") is not None:
        blockers_raw = _field(value, "blocked_metrics", "blockers", default=()) or ()
        blockers = tuple(
            item
            if isinstance(item, ExpansionBlocker)
            else ExpansionBlocker(
                _clean_id(_field(item, "window_id", "window"), "unknown-window"),
                _clean_id(_field(item, "metric_id", "metric"), "unknown-metric"),
                str(_field(item, "reason", default=REASON_INSUFFICIENT_DATA)).lower(),
                str(_field(item, "detail", default="")),
            )
            for item in blockers_raw
        )
        return ExpansionDecision(
            expansion_eligible=bool(_field(value, "expansion_eligible", "eligible")),
            reason=str(_field(value, "reason", default=REASON_INSUFFICIENT_DATA)).lower(),
            windows_evaluated=int(_field(value, "windows_evaluated", "window_count", default=0) or 0),
            window_ids=tuple(_field(value, "window_ids", default=()) or ()),
            blocked_windows=tuple(_field(value, "blocked_windows", default=()) or ()),
            blocked_metrics=blockers,
            recommendations=_field(value, "recommendations", "expansion_recommendations", default={}) or {},
            recalibration=_field(value, "recalibration", "threshold_recommendations", default={}) or {},
        )
    if isinstance(value, bool):
        return ExpansionDecision(
            expansion_eligible=value,
            reason=REASON_MEETS_TARGET if value else REASON_INSUFFICIENT_DATA,
            windows_evaluated=REQUIRED_WINDOW_COUNT if value else 0,
        )
    return evaluate_four_week_expansion(value)


def _decision_reason(decision: ExpansionDecision) -> str:
    if decision.expansion_eligible:
        return "four consecutive weekly windows and all six metrics meet_target"
    blocked = ", ".join(
        f"{item.window_id}/{item.metric_id}" for item in decision.blocked_metrics[:8]
    )
    if blocked:
        return f"{decision.reason}; blocked window/metric: {blocked}"
    return f"{decision.reason}; expansion remains blocked"


def _source_candidates(source_policy: Any) -> list[Any]:
    sources = _field(source_policy, "sources", default=None)
    if sources is None and isinstance(source_policy, Sequence) and not isinstance(
        source_policy, (str, bytes, bytearray)
    ):
        sources = source_policy
    if isinstance(sources, Mapping):
        sources = list(sources.values())
    if not isinstance(sources, Iterable) or isinstance(sources, (str, bytes, bytearray)):
        return []
    return [
        source
        for source in sources
        if _field(source, "fixture", default=False) is not True
        and _field(source, "active", default=False) is not True
    ]


def build_expansion_recommendations(
    decision: Any,
    source_policy: Any = None,
    *,
    settings: Any = None,
) -> dict[str, dict[str, Any]]:
    """Create recommendation/hold records for all three expansion candidates."""

    result = _coerce_decision(decision)
    basis = _decision_reason(result)
    recommendations: dict[str, dict[str, Any]] = {}

    if result.expansion_eligible:
        auto_action = RECOMMEND
        auto_reason = f"{basis}; publication policy change still requires explicit approval"
        search_action = RECOMMEND
        search_reason = f"{basis}; search UI scope can be reviewed as a follow-up"
        candidates = _source_candidates(source_policy)
        if source_policy is None:
            source_action = HOLD
            source_reason = "Source Policy was not supplied; real-source activation cannot be assessed"
        elif not candidates:
            source_action = HOLD
            source_reason = "Source Policy has no inactive real-source candidate available for review"
        else:
            source_action = RECOMMEND
            source_reason = (
                f"{basis}; Source Policy has {len(candidates)} inactive real-source candidate(s); "
                "human approval, terms, robots, license, and rate-limit review remain required"
            )
    else:
        action = HOLD
        blocked_reason = f"{basis}; measurement gate is not satisfied"
        auto_action = search_action = source_action = action
        auto_reason = search_reason = source_reason = blocked_reason

    payloads = {
        "auto_publish": (auto_action, auto_reason),
        "real_source_activation": (source_action, source_reason),
        "search_ui": (search_action, search_reason),
    }
    for candidate in EXPANSION_CANDIDATES:
        action, reason = payloads[candidate]
        recommendations[candidate] = {
            "candidate": candidate,
            "recommendation": action,
            "decision": action,
            "action": action,
            "reason": reason,
            "expansion_eligible": result.expansion_eligible,
            "blocked_windows": result.blocked_windows,
            "blocked_metrics": [item.as_dict() for item in result.blocked_metrics],
        }
    return recommendations


def recommend_threshold_recalibration(
    decision: Any = None,
    current_settings: Any = None,
    *,
    settings: Any = None,
    proposed_values: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return six recommendation-only Appendix A threshold records.

    No recalibration formula is authorized by the intent.  Therefore the
    default proposal is the current Appendix A value and the action is
    ``hold``.  Callers may provide an explicit ``proposed_values`` mapping for
    review; this function still only reports it and never writes configuration.
    """

    if decision is None:
        decision = evaluate_four_week_expansion([])
    result = _coerce_decision(decision)
    selected_settings = current_settings if current_settings is not None else settings
    proposed = proposed_values or {}
    output: dict[str, dict[str, Any]] = {}
    for key, default in APPENDIX_A_RECALIBRATION_DEFAULTS.items():
        current = _field(selected_settings, key, default=default)
        if current is None:
            current = default
        suggestion = proposed.get(key, current)
        action = RECOMMEND if result.expansion_eligible and suggestion != current else HOLD
        if action == RECOMMEND:
            reason = (
                "four-week gate passed; proposed value is returned for user review only; "
                "configuration was not changed"
            )
        elif result.expansion_eligible:
            reason = (
                "four-week gate passed, but no authorized value-change formula was supplied; "
                "retain current Appendix A value"
            )
        else:
            reason = f"{_decision_reason(result)}; retain current Appendix A value"
        output[key] = {
            "key": key,
            "current_value": current,
            "current": current,
            "recommended_value": suggestion,
            "recommended": suggestion,
            "proposed_value": suggestion,
            "value": suggestion,
            "recommendation": action,
            "decision": action,
            "action": action,
            "changed": False,
            "reason": reason,
        }
    return output


@dataclass(frozen=True, slots=True)
class ExpansionAssessment(Mapping[str, Any]):
    """Combined four-week decision, candidate recommendations, and thresholds."""

    decision: ExpansionDecision
    recommendations: Mapping[str, Mapping[str, Any]]
    recalibration: Mapping[str, Mapping[str, Any]]

    @property
    def expansion_eligible(self) -> bool:
        return self.decision.expansion_eligible

    @property
    def reason(self) -> str:
        return self.decision.reason

    @property
    def blocked_windows(self) -> tuple[str, ...]:
        return self.decision.blocked_windows

    @property
    def blocked_metrics(self) -> tuple[ExpansionBlocker, ...]:
        return self.decision.blocked_metrics

    def as_dict(self) -> dict[str, Any]:
        payload = self.decision.as_dict()
        payload["recommendations"] = dict(self.recommendations)
        payload["expansion_recommendations"] = dict(self.recommendations)
        payload["recalibration"] = dict(self.recalibration)
        payload["threshold_recommendations"] = dict(self.recalibration)
        return payload

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        if key == "decision":
            return self.decision
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("decision", *self.as_dict().keys()))

    def __len__(self) -> int:
        return len(self.as_dict()) + 1


def evaluate_expansion(
    observations: Any = None,
    source_policy: Any = None,
    *,
    settings: Any = None,
    current_settings: Any = None,
    proposed_values: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> ExpansionAssessment:
    """Run the complete read-only expansion assessment boundary."""

    decision = evaluate_four_week_expansion(observations, **kwargs)
    recommendations = build_expansion_recommendations(
        decision,
        source_policy,
        settings=settings,
    )
    recalibration = recommend_threshold_recalibration(
        decision,
        current_settings if current_settings is not None else settings,
        proposed_values=proposed_values,
    )
    return ExpansionAssessment(decision, recommendations, recalibration)


# Stable aliases for callers that use slightly different package-E names.
assess_four_week_expansion = evaluate_four_week_expansion
evaluate_expansion_eligibility = evaluate_four_week_expansion
assess_expansion_eligibility = evaluate_four_week_expansion
check_expansion_eligibility = evaluate_four_week_expansion
four_week_expansion_check = evaluate_four_week_expansion
make_expansion_recommendations = build_expansion_recommendations
recommend_expansions = build_expansion_recommendations
generate_expansion_recommendations = build_expansion_recommendations
recalibration_recommendations = recommend_threshold_recalibration
recommend_recalibration = recommend_threshold_recalibration
build_threshold_recommendations = recommend_threshold_recalibration
assess_expansion = evaluate_expansion


__all__ = [
    "APPENDIX_A_DEFAULTS",
    "APPENDIX_A_RECALIBRATION_DEFAULTS",
    "APPENDIX_A_RECALIBRATION_KEYS",
    "APPENDIX_A_VALUES",
    "EXPANSION_CANDIDATES",
    "HOLD",
    "REASON_BREACHED",
    "REASON_INSUFFICIENT_DATA",
    "REASON_MEETS_TARGET",
    "RECALIBRATION_KEYS",
    "RECOMMEND",
    "REQUIRED_METRIC_IDS",
    "REQUIRED_WINDOW_COUNT",
    "ExpansionAssessment",
    "ExpansionBlocker",
    "ExpansionDecision",
    "assess_expansion",
    "assess_expansion_eligibility",
    "assess_four_week_expansion",
    "build_expansion_recommendations",
    "build_threshold_recommendations",
    "check_expansion_eligibility",
    "evaluate_expansion",
    "evaluate_expansion_eligibility",
    "evaluate_four_week_expansion",
    "four_week_expansion_check",
    "generate_expansion_recommendations",
    "make_expansion_recommendations",
    "recalibration_recommendations",
    "recommend_expansions",
    "recommend_recalibration",
    "recommend_threshold_recalibration",
]
