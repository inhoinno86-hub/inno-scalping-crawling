"""Deterministic, explainable value scoring for strategy candidates.

The scorer is deliberately a small, local rule set.  It does not call an LLM,
read a new source, create storage artifacts, or require an embedding index.
Scores are written only to the existing ``value_score`` and
``value_score_breakdown`` candidate fields.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any


VALUE_SCORE_WEIGHTS: dict[str, int] = {
    "source_reliability": 30,
    "reproducibility": 25,
    "ultra_short_term_relevance": 20,
    "recency": 15,
    "novelty": 10,
}

SCORING_CRITERIA = tuple(VALUE_SCORE_WEIGHTS)

_MISSING = object()
_CONFLICTING = "conflicting"
_UNKNOWN = {"", "unknown", "not_applicable", "none", "null"}

_OFFICIAL_SOURCE_TERMS = (
    "official",
    "exchange",
    "broker",
    "government",
    "regulator",
    "university",
    "academic",
    "peer-reviewed",
    "peer reviewed",
    "research institution",
)
_MICROSTRUCTURE_TERMS = (
    "microstructure",
    "order book",
    "orderbook",
    "order flow",
    "order-flow",
    "queue",
    "imbalance",
    "l1",
    "l2",
    "l3",
    "tick",
    "spread",
    "market depth",
    "liquidity",
)
_ULTRA_SHORT_TERMS = (
    "millisecond",
    "milliseconds",
    "sub-second",
    "subsecond",
    "tick",
    "seconds",
    "second",
    "minutes",
    "minute",
    "ultra-short",
    "ultrashort",
)
_SHORT_TERMS = ("intraday", "hour", "hours", "short horizon", "short-horizon")
_LONG_TERMS = (
    "day",
    "days",
    "week",
    "weeks",
    "month",
    "months",
    "long-term",
    "long term",
)
_FAST_FREQUENCY_TERMS = (
    "tick",
    "sub-second",
    "subsecond",
    "millisecond",
    "second",
    "1m",
    "1 min",
    "minute",
)

_NOVELTY_SCORES = {
    "new": 10,
    "new_evidence": 8,
    "changed": 8,
    "variant": 6,
    "duplicate": 0,
    "unknown": 5,
}


class ScoringError(ValueError):
    """Raised when a candidate cannot be scored or persisted."""


@dataclass(slots=True)
class ValueScoreResult:
    """Value score plus the exact breakdown persisted on the candidate."""

    candidate: Any
    value_score: int
    value_score_breakdown: dict[str, dict[str, Any]]
    novelty_status: str | None = None
    related_strategy_ids: tuple[str, ...] = ()

    @property
    def score(self) -> int:
        """Compatibility alias for callers that use ``score``."""

        return self.value_score

    @property
    def breakdown(self) -> dict[str, dict[str, Any]]:
        """Compatibility alias for the persisted breakdown."""

        return self.value_score_breakdown

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": _field(self.candidate, "candidate_id"),
            "value_score": self.value_score,
            "value_score_breakdown": self.value_score_breakdown,
            "novelty_status": self.novelty_status,
            "related_strategy_ids": list(self.related_strategy_ids),
        }

    def __getitem__(self, key: str) -> Any:
        if key in {"value_score", "score"}:
            return self.value_score
        if key in {"value_score_breakdown", "breakdown"}:
            return self.value_score_breakdown
        if key == "candidate":
            return self.candidate
        if key == "novelty_status":
            return self.novelty_status
        if key == "related_strategy_ids":
            return list(self.related_strategy_ids)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return bool(value)
    return True


def _text(value: Any) -> str:
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, str):
        return " ".join(value.split()).strip()
    if isinstance(value, Mapping):
        return " ".join(
            part for part in (_text(item) for item in value.values()) if part
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return " ".join(part for part in (_text(item) for item in value) if part)
    return str(value).strip()


def _status(candidate: Any, field_name: str) -> str:
    value = _field(candidate, f"{field_name}_status", "")
    return _text(value).casefold()


def _status_quality(candidate: Any, field_name: str) -> int:
    """Return 0..2 for absent/conflicting, inferred, or explicit evidence."""

    value = _field(candidate, field_name)
    if not _present(value):
        return 0
    status = _status(candidate, field_name)
    if status == _CONFLICTING:
        return 0
    if status in _UNKNOWN:
        return 1
    if status == "inferred":
        return 1
    return 2


def _mapping_values(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    result: list[Mapping[str, Any]] = [value]
    for key in (
        "metadata",
        "metadata_json",
        "source",
        "source_metadata",
        "provenance",
        "document",
        "document_version",
    ):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            result.append(nested)
    return result


def _lookup(
    candidate: Any,
    document_version: Any,
    *names: str,
    default: Any = None,
) -> Any:
    """Read direct fields first, then the existing metadata/provenance fields."""

    records: list[Any] = []
    seen: set[int] = set()

    def add_record(record: Any) -> None:
        if record is None or record is _MISSING or id(record) in seen:
            return
        seen.add(id(record))
        records.append(record)

    add_record(candidate)
    add_record(document_version)
    nested_names = (
        "metadata",
        "metadata_json",
        "source",
        "source_metadata",
        "provenance",
        "document",
        "document_version",
    )
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        for nested_name in nested_names:
            nested = _field(record, nested_name, _MISSING)
            if nested is _MISSING:
                continue
            add_record(nested)
            for nested_mapping in _mapping_values(nested):
                add_record(nested_mapping)

    for name in names:
        for record in records:
            if isinstance(record, Mapping):
                value = record.get(name, _MISSING)
            else:
                value = getattr(record, name, _MISSING)
            if value is not _MISSING and _present(value):
                return value
    return default


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(number):
        return None
    return number


def _clamp_score(value: int | float, maximum: int) -> int:
    return max(0, min(maximum, int(round(value))))


def _criterion(maximum: int, score: int | float, reason: str) -> dict[str, Any]:
    bounded = _clamp_score(score, maximum)
    message = reason.strip() or "No scoring evidence was identified."
    return {
        "score": bounded,
        "max_score": maximum,
        "reason": message,
        "rationale": message,
    }


def _contains_any(value: Any, terms: Sequence[str]) -> bool:
    lowered = _text(value).casefold()
    return any(term.casefold() in lowered for term in terms)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            result = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _as_of(value: Any) -> datetime:
    parsed = _parse_datetime(value)
    return parsed if parsed is not None else datetime.now(UTC)


def _source_reliability_score(
    candidate: Any,
    document_version: Any,
) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    source_type = _lookup(
        candidate,
        document_version,
        "source_type",
        "source_kind",
        "source_category",
        "publisher_type",
        "type",
        "name",
    )
    official_flag = _lookup(candidate, document_version, "official", "is_official")
    confidence = _number(
        _lookup(candidate, document_version, "source_confidence", "source_reliability")
    )
    if confidence is not None and 0 <= confidence <= 1:
        points = _clamp_score(confidence * 10, 10)
        score += points
        reasons.append(f"source_confidence contributes {points}/10")
    elif (
        official_flag is True
        or _text(official_flag).casefold() in {"true", "yes", "1"}
        or _contains_any(source_type, _OFFICIAL_SOURCE_TERMS)
    ):
        score += 10
        reasons.append("official or institutional source identified: +10/10")
    elif _present(source_type):
        score += 6
        reasons.append("source category identified but officialness is unconfirmed: +6/10")
    else:
        reasons.append("officialness is not identified: +0/10")

    author = _lookup(
        candidate,
        document_version,
        "author_or_org",
        "author",
        "publisher",
        "organization",
        "institution",
    )
    if _present(author):
        score += 5
        reasons.append("author or organisation is identified: +5/5")
    else:
        reasons.append("author or organisation is not identified: +0/5")

    published = _lookup(
        candidate,
        document_version,
        "published_at",
        "publication_date",
        "issued_at",
        "created_at",
    )
    if _parse_datetime(published) is not None:
        score += 4
        reasons.append("publication time is identifiable: +4/4")
    else:
        reasons.append("publication time is not identifiable: +0/4")

    license_value = _lookup(candidate, document_version, "license", "licence")
    if _present(license_value):
        score += 3
        reasons.append("license is recorded: +3/3")
    else:
        reasons.append("license is not recorded: +0/3")

    traceability = _lookup(
        candidate,
        document_version,
        "canonical_url",
        "source_url",
        "original_url",
        "document_version_id",
        "source_version_ref",
        "source_id",
    )
    if _present(traceability):
        score += 8
        reasons.append("source URL or version identifier is available: +8/8")
    else:
        reasons.append("source URL or version identifier is unavailable: +0/8")

    return _criterion(30, score, "; ".join(reasons))


def _reproducibility_score(candidate: Any, document_version: Any) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    for field_name, maximum, label in (
        ("core_hypothesis", 2, "core hypothesis"),
        ("signal_inputs", 2, "signal inputs"),
        ("entry_logic", 3, "entry logic"),
        ("exit_logic", 3, "exit logic"),
    ):
        quality = _status_quality(candidate, field_name)
        points = {0: 0, 1: max(1, maximum // 2), 2: maximum}.get(quality, 0)
        score += points
        reasons.append(f"{label} quality {points}/{maximum}")

    required_data_quality = _status_quality(candidate, "required_data")
    data_points = {0: 0, 1: 2, 2: 4}.get(required_data_quality, 0)
    score += data_points
    reasons.append(f"required data quality {data_points}/4")

    frequency = _lookup(
        candidate,
        document_version,
        "required_frequency",
        "data_frequency",
        "sampling_frequency",
        "timeframe",
    )
    frequency_points = 2 if _present(frequency) else 0
    score += frequency_points
    reasons.append(f"data frequency {'identified' if frequency_points else 'not identified'}: {frequency_points}/2")

    market_scope = _lookup(
        candidate,
        document_version,
        "market_types",
        "asset_classes",
        "market_scope",
        "venue",
    )
    market_points = 2 if _present(market_scope) else 0
    score += market_points
    reasons.append(f"market scope {market_points}/2")

    horizon = _lookup(
        candidate,
        document_version,
        "holding_horizon",
        "time_horizon",
        "holding_period",
    )
    horizon_points = 2 if _present(horizon) else 0
    score += horizon_points
    reasons.append(f"holding horizon {horizon_points}/2")

    range_value = _lookup(
        candidate,
        document_version,
        "time_range",
        "data_window",
        "observation_window",
        "sample_period",
    )
    range_points = 1 if _present(range_value) else 0
    score += range_points
    reasons.append(f"time or observation range {range_points}/1")

    statuses = [
        _status_quality(candidate, field_name)
        for field_name in (
            "core_hypothesis",
            "signal_inputs",
            "entry_logic",
            "exit_logic",
            "required_data",
            "risk_notes",
        )
    ]
    status_points = 4 if statuses and all(item == 2 for item in statuses) else 2 if any(statuses) else 0
    score += status_points
    reasons.append(f"core field status completeness {status_points}/4")

    return _criterion(25, score, "; ".join(reasons))


def _relevance_score(candidate: Any, document_version: Any) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    horizon = _lookup(
        candidate,
        document_version,
        "holding_horizon",
        "time_horizon",
        "holding_period",
    )
    if _contains_any(horizon, _ULTRA_SHORT_TERMS):
        horizon_points = 6
        horizon_reason = "seconds, minutes, ticks, or another ultra-short horizon is explicit"
    elif _contains_any(horizon, _SHORT_TERMS):
        horizon_points = 3
        horizon_reason = "short horizon is present but ultra-short duration is not explicit"
    elif _contains_any(horizon, _LONG_TERMS):
        horizon_points = 0
        horizon_reason = "horizon is outside ultra-short scope"
    else:
        horizon_points = 0
        horizon_reason = "holding horizon is not identified"
    score += horizon_points
    reasons.append(f"holding horizon {horizon_points}/6: {horizon_reason}")

    required_data = _lookup(candidate, document_version, "required_data")
    frequency = _lookup(
        candidate,
        document_version,
        "required_frequency",
        "data_frequency",
        "sampling_frequency",
        "timeframe",
    )
    data_points = 0
    if _present(required_data):
        data_points += 3
    if _contains_any(frequency, _FAST_FREQUENCY_TERMS):
        data_points += 2
    score += data_points
    reasons.append(
        f"high-frequency data fit {data_points}/5: "
        f"required_data={'present' if _present(required_data) else 'absent'}, "
        f"fast_frequency={'present' if _contains_any(frequency, _FAST_FREQUENCY_TERMS) else 'absent'}"
    )

    microstructure = _lookup(
        candidate,
        document_version,
        "microstructure_level",
        "market_types",
        "tags",
        "strategy_families",
    )
    micro_points = 4 if _contains_any(microstructure, _MICROSTRUCTURE_TERMS) else 0
    score += micro_points
    reasons.append(f"microstructure fit {micro_points}/4")

    entry_quality = _status_quality(candidate, "entry_logic")
    exit_quality = _status_quality(candidate, "exit_logic")
    logic_points = (2 if entry_quality == 2 else 1 if entry_quality == 1 else 0) + (
        1 if exit_quality == 2 else 0
    )
    score += logic_points
    reasons.append(f"entry/exit logic fit {logic_points}/3")

    relevance_status = _text(_field(candidate, "relevance_status", "")).casefold()
    relevance_points = {"relevant": 2, "background_only": 1}.get(relevance_status, 0)
    score += relevance_points
    reasons.append(f"relevance_status={relevance_status or 'unknown'}: {relevance_points}/2")

    return _criterion(20, score, "; ".join(reasons))


def _recency_score(
    candidate: Any,
    document_version: Any,
    *,
    as_of: datetime,
    last_successful_briefing_at: Any = None,
) -> dict[str, Any]:
    timestamp_values = [
        _lookup(
            candidate,
            document_version,
            name,
        )
        for name in (
            "updated_at",
            "last_updated_at",
            "modified_at",
            "published_at",
            "publication_date",
            "issued_at",
            "retrieved_at",
        )
    ]
    timestamps = [parsed for parsed in (_parse_datetime(item) for item in timestamp_values) if parsed]
    latest = max(timestamps) if timestamps else None
    if latest is None:
        return _criterion(
            15,
            0,
            "No publication, update, or retrieval timestamp is identifiable: +0/15",
        )

    age_days = max(0.0, (as_of - latest).total_seconds() / 86400)
    if age_days <= 1:
        timing_points = 10
    elif age_days <= 7:
        timing_points = 8
    elif age_days <= 14:
        timing_points = 6
    elif age_days <= 30:
        timing_points = 4
    elif age_days <= 90:
        timing_points = 2
    else:
        timing_points = 0
    reasons = [f"latest identifiable timestamp age {age_days:.1f} days: +{timing_points}/10"]

    comparison_value = last_successful_briefing_at
    if comparison_value is None:
        comparison_value = _lookup(
            candidate,
            document_version,
            "last_successful_briefing_at",
            "last_successful_briefing",
            "last_briefing_at",
        )
    comparison = _parse_datetime(comparison_value)
    if comparison is not None and latest > comparison:
        change_points = 5
        reasons.append("document timestamp is newer than last successful briefing: +5/5")
    elif comparison is not None:
        change_points = 0
        reasons.append("no document change after last successful briefing: +0/5")
    else:
        change_points = 0
        reasons.append("last successful briefing timestamp is unavailable: +0/5")

    return _criterion(15, timing_points + change_points, "; ".join(reasons))


def _normalise_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", _text(value).casefold()).strip()


def _token_set(value: Any) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9가-힣]+", _text(value).casefold()) if token}


def _candidate_similarity(left: Any, right: Any) -> float:
    fields = (
        "canonical_name",
        "strategy_families",
        "asset_classes",
        "holding_horizon",
        "core_hypothesis",
        "signal_inputs",
        "entry_logic",
        "exit_logic",
        "required_data",
    )
    left_tokens = _token_set(" ".join(_text(_field(left, name, "")) for name in fields))
    right_tokens = _token_set(" ".join(_text(_field(right, name, "")) for name in fields))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _candidate_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("candidates", "items", "related_candidates"):
            nested = value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
                return list(nested)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _novelty_score(
    candidate: Any,
    existing_candidates: Sequence[Any] | None,
) -> tuple[dict[str, Any], str | None, tuple[str, ...]]:
    novelty_value = _text(_field(candidate, "novelty_status", "")).casefold()
    raw_related_ids = _field(candidate, "related_strategy_ids", []) or []
    if isinstance(raw_related_ids, str):
        raw_related_ids = [raw_related_ids]
    related_ids = tuple(
        sorted({_text(item) for item in raw_related_ids if _present(item)})
    )
    if novelty_value in _NOVELTY_SCORES:
        score = _NOVELTY_SCORES[novelty_value]
        reason = f"existing novelty_status={novelty_value}: {score}/{VALUE_SCORE_WEIGHTS['novelty']}"
        return _criterion(10, score, reason), novelty_value, related_ids

    comparisons = []
    candidate_id = _text(_field(candidate, "candidate_id", ""))
    for other in _candidate_list(existing_candidates):
        other_id = _text(_field(other, "candidate_id", ""))
        if candidate_id and other_id and candidate_id == other_id:
            continue
        comparisons.append(other)

    if comparisons:
        candidate_name = _normalise_name(_field(candidate, "canonical_name", ""))
        exact = [
            other
            for other in comparisons
            if candidate_name
            and candidate_name == _normalise_name(_field(other, "canonical_name", ""))
        ]
        if exact:
            ids = tuple(
                sorted(
                    {
                        _text(_field(other, "strategy_id", _field(other, "candidate_id", "")))
                        for other in exact
                        if _present(_field(other, "strategy_id", _field(other, "candidate_id", "")))
                    }
                )
            )
            return (
                _criterion(10, 0, "normalised canonical name matches existing candidate: 0/10"),
                "duplicate",
                ids,
            )
        similarities = sorted(
            ((_candidate_similarity(candidate, other), other) for other in comparisons),
            key=lambda item: (-item[0], _text(_field(item[1], "candidate_id", ""))),
        )
        best_similarity, best = similarities[0]
        best_id = _text(_field(best, "strategy_id", _field(best, "candidate_id", "")))
        ids = (best_id,) if best_id else ()
        if best_similarity >= 0.65:
            return (
                _criterion(
                    10,
                    3,
                    f"deterministic field similarity with existing candidate is {best_similarity:.2f}: 3/10",
                ),
                "variant",
                ids,
            )
        if best_similarity >= 0.35:
            return (
                _criterion(
                    10,
                    6,
                    f"deterministic field similarity with existing candidate is {best_similarity:.2f}: 6/10",
                ),
                "variant",
                ids,
            )
        return (
            _criterion(
                10,
                10,
                f"no material deterministic field match; best similarity is {best_similarity:.2f}: 10/10",
            ),
            "new",
            (),
        )

    if related_ids:
        return (
            _criterion(10, 4, "related_strategy_ids are present but novelty status is unknown: 4/10"),
            "unknown",
            related_ids,
        )
    return (
        _criterion(10, 5, "novelty status and comparison set are unavailable; neutral score: 5/10"),
        "unknown",
        (),
    )


def _unwrap_candidate(candidate: Any, document_version: Any) -> tuple[Any, Any]:
    inner = _field(candidate, "candidate", _MISSING)
    if inner is not _MISSING and inner is not None:
        if document_version is None:
            document_version = _field(candidate, "document_version", None)
        candidate = inner
    if candidate is None:
        raise ScoringError("candidate is required")
    return candidate, document_version


def _store(candidate: Any, name: str, value: Any) -> None:
    if isinstance(candidate, Mapping):
        try:
            candidate[name] = value  # type: ignore[index]
        except TypeError as exc:
            raise ScoringError("candidate mapping must be mutable") from exc
        return
    try:
        setattr(candidate, name, value)
    except (AttributeError, TypeError) as exc:
        raise ScoringError(f"candidate field {name!r} is not writable") from exc


def score_candidate(
    candidate: Any,
    document_version: Any = None,
    existing_candidates: Sequence[Any] | None = None,
    *,
    document: Any = None,
    related_candidates: Sequence[Any] | None = None,
    as_of: Any = None,
    now: Any = None,
    last_successful_briefing_at: Any = None,
    persist: bool = True,
) -> ValueScoreResult:
    """Score and optionally persist one candidate using fixed local rules.

    ``document_version`` and ``existing_candidates`` are optional context.  If
    ``as_of`` is supplied, scoring is fully reproducible across runs; omitted
    ``as_of`` means current UTC time for recency scoring.  The input candidate
    may be a mutable mapping, an ORM object, or a validation result exposing a
    ``candidate`` attribute.
    """

    if document is not None:
        if document_version is not None and document_version is not document:
            raise ScoringError("provide either document or document_version, not both")
        document_version = document
    if now is not None:
        if as_of is not None:
            raise ScoringError("provide either as_of or now, not both")
        as_of = now
    if related_candidates is not None:
        if existing_candidates is not None and existing_candidates is not related_candidates:
            raise ScoringError(
                "provide either existing_candidates or related_candidates, not both"
            )
        existing_candidates = related_candidates

    candidate, document_version = _unwrap_candidate(candidate, document_version)
    as_of_datetime = _as_of(as_of)

    breakdown: dict[str, dict[str, Any]] = {
        "source_reliability": _source_reliability_score(candidate, document_version),
        "reproducibility": _reproducibility_score(candidate, document_version),
        "ultra_short_term_relevance": _relevance_score(candidate, document_version),
        "recency": _recency_score(
            candidate,
            document_version,
            as_of=as_of_datetime,
            last_successful_briefing_at=last_successful_briefing_at,
        ),
    }
    novelty_detail, novelty_status, related_ids = _novelty_score(
        candidate,
        existing_candidates,
    )
    breakdown["novelty"] = novelty_detail

    value_score = sum(detail["score"] for detail in breakdown.values())
    value_score = _clamp_score(value_score, 100)
    if value_score != sum(detail["score"] for detail in breakdown.values()):
        raise ScoringError("value score exceeds breakdown sum")

    if persist:
        _store(candidate, "value_score", value_score)
        _store(candidate, "value_score_breakdown", breakdown)

    return ValueScoreResult(
        candidate=candidate,
        value_score=value_score,
        value_score_breakdown=breakdown,
        novelty_status=novelty_status,
        related_strategy_ids=related_ids,
    )


def apply_value_score(
    candidate: Any,
    document_version: Any = None,
    existing_candidates: Sequence[Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Score candidate and return candidate object for pipeline-style callers."""

    return score_candidate(
        candidate,
        document_version,
        existing_candidates,
        **kwargs,
    ).candidate


def calculate_value_score(*args: Any, **kwargs: Any) -> ValueScoreResult:
    """Compatibility entry point for callers naming the calculation directly."""

    return score_candidate(*args, **kwargs)


compute_value_score = calculate_value_score
score_value = score_candidate
score_strategy_candidate = score_candidate
score_candidate_value = score_candidate
ScoringResult = ValueScoreResult
ValueScoringResult = ValueScoreResult


__all__ = [
    "SCORING_CRITERIA",
    "VALUE_SCORE_WEIGHTS",
    "ScoringError",
    "ValueScoreResult",
    "ValueScoringResult",
    "ScoringResult",
    "apply_value_score",
    "calculate_value_score",
    "compute_value_score",
    "score_candidate",
    "score_candidate_value",
    "score_strategy_candidate",
    "score_value",
]
