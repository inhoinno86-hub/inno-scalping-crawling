"""Deterministic strategy-candidate novelty routing.

The router compares only fields already present on a strategy candidate.  It
does not call an LLM, create a search index, or calculate an embedding.  The
comparison is intentionally small and explainable so the same candidate set
always produces the same status and related IDs.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


NOVELTY_STATUSES = (
    "new",
    "new_evidence",
    "changed",
    "variant",
    "duplicate",
)

CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)

_MISSING = object()
_CORE_RELATED_THRESHOLD = 0.35
_CORE_CHANGED_THRESHOLD = 0.50

# The order is used only to make holding-horizon ranges comparable.  It is
# not a score and is never persisted.
_HORIZON_UNITS = {
    "millisecond": 0,
    "milliseconds": 0,
    "millis": 0,
    "ms": 0,
    "subsecond": 0,
    "second": 1,
    "seconds": 1,
    "sec": 1,
    "secs": 1,
    "minute": 2,
    "minutes": 2,
    "min": 2,
    "mins": 2,
    "hour": 3,
    "hours": 3,
    "hr": 3,
    "hrs": 3,
    "day": 4,
    "days": 4,
    "week": 5,
    "weeks": 5,
    "month": 6,
    "months": 6,
}
_HORIZON_WORD_RE = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(_HORIZON_UNITS, key=len, reverse=True)) + r")(?![a-z])"
)


@dataclass(frozen=True, slots=True)
class NoveltyResult:
    """The existing storage contract produced by one novelty comparison."""

    novelty_status: str
    related_strategy_ids: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """Compatibility alias for callers that use ``status``."""

        return self.novelty_status

    @property
    def related_ids(self) -> tuple[str, ...]:
        """Compatibility alias for callers that use ``related_ids``."""

        return self.related_strategy_ids

    def as_dict(self) -> dict[str, Any]:
        return {
            "novelty_status": self.novelty_status,
            "related_strategy_ids": list(self.related_strategy_ids),
        }

    def __getitem__(self, key: str) -> Any:
        if key == "novelty_status":
            return self.novelty_status
        if key in {"related_strategy_ids", "related_ids"}:
            return list(self.related_strategy_ids)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


def _field(record: Any, name: str, default: Any = _MISSING) -> Any:
    if record is None:
        return default
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _unwrap_candidate(record: Any) -> Any:
    """Accept a validation/scoring result without adding a new contract."""

    inner = _field(record, "candidate")
    if inner is not _MISSING and inner is not None:
        return inner
    return record


def _normalise_text(value: Any) -> str:
    if value is None or value is _MISSING:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = str(value)
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char if char.isalnum() else " " for char in text)


def _normalised_text(value: Any) -> str:
    return " ".join(_normalise_text(value).split())


def _tokens(value: Any) -> frozenset[str]:
    if value is None or value is _MISSING:
        return frozenset()
    if isinstance(value, Mapping):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_tokens(item))
        return frozenset(parts)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts = []
        for item in value:
            parts.extend(_tokens(item))
        return frozenset(parts)
    return frozenset(_normalised_text(value).split())


def _normalised_collection(value: Any) -> frozenset[str]:
    if value is None or value is _MISSING:
        return frozenset()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = [value]
    return frozenset(
        normalised
        for item in values
        if (normalised := _normalised_text(item))
    )


def _horizon_range(value: Any) -> tuple[int, int] | None:
    text = _normalised_text(value)
    if not text:
        return None
    matches = [
        _HORIZON_UNITS[match.group(1)]
        for match in _HORIZON_WORD_RE.finditer(text)
    ]
    if not matches:
        return None
    return min(matches), max(matches)


def _same_horizon(left: Any, right: Any) -> bool:
    left_range = _horizon_range(left)
    right_range = _horizon_range(right)
    if left_range is not None and right_range is not None:
        return left_range == right_range
    return _normalised_text(left) == _normalised_text(right)


def _horizon_overlaps(left: Any, right: Any) -> bool:
    left_range = _horizon_range(left)
    right_range = _horizon_range(right)
    if left_range is None or right_range is None:
        return bool(
            _normalised_text(left)
            and _normalised_text(left) == _normalised_text(right)
        )
    return max(left_range[0], right_range[0]) <= min(left_range[1], right_range[1])


def _profile(record: Any) -> tuple[frozenset[str], frozenset[str], Any]:
    return (
        _normalised_collection(_field(record, "strategy_families")),
        _normalised_collection(_field(record, "asset_classes")),
        _field(record, "holding_horizon", ""),
    )


def _same_profile(left: Any, right: Any) -> bool:
    left_families, left_assets, left_horizon = _profile(left)
    right_families, right_assets, right_horizon = _profile(right)
    return (
        left_families == right_families
        and left_assets == right_assets
        and _same_horizon(left_horizon, right_horizon)
    )


def _profile_related(left: Any, right: Any) -> bool:
    left_families, left_assets, left_horizon = _profile(left)
    right_families, right_assets, right_horizon = _profile(right)
    if not left_families or not right_families:
        return False
    if not left_assets or not right_assets:
        return False
    return bool(
        left_families & right_families
        and left_assets & right_assets
        and _horizon_overlaps(left_horizon, right_horizon)
    )


def core_field_similarity(left: Any, right: Any) -> float:
    """Return the mean token-Jaccard similarity across the six core fields."""

    similarities: list[float] = []
    for field_name in CORE_FIELDS:
        left_tokens = _tokens(_field(left, field_name))
        right_tokens = _tokens(_field(right, field_name))
        if not left_tokens and not right_tokens:
            similarities.append(1.0)
        elif not left_tokens or not right_tokens:
            similarities.append(0.0)
        else:
            similarities.append(
                len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
            )
    return round(sum(similarities) / len(similarities), 6)


def _same_canonical_name(left: Any, right: Any) -> bool:
    left_name = _normalised_text(_field(left, "canonical_name"))
    right_name = _normalised_text(_field(right, "canonical_name"))
    return bool(left_name) and left_name == right_name


def _record_id(record: Any) -> str:
    for field_name in ("strategy_id", "candidate_id"):
        value = _field(record, field_name)
        if value is not _MISSING and value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _candidate_id(record: Any) -> str:
    value = _field(record, "candidate_id")
    if value is _MISSING or value is None:
        return ""
    return str(value).strip()


def _strategy_id(record: Any) -> str:
    value = _field(record, "strategy_id")
    if value is _MISSING or value is None:
        return ""
    return str(value).strip()


def _version_ids(record: Any) -> frozenset[str]:
    value = _field(record, "document_version_ids")
    if value is _MISSING or value is None:
        value = _field(record, "document_version_id")
    if value is _MISSING or value is None:
        return frozenset()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        values = [value]
    return frozenset(str(item).strip() for item in values if str(item).strip())


def _same_record(left: Any, right: Any) -> bool:
    left_candidate = _candidate_id(left)
    right_candidate = _candidate_id(right)
    if left_candidate and right_candidate and left_candidate == right_candidate:
        return True
    left_strategy = _strategy_id(left)
    right_strategy = _strategy_id(right)
    return bool(left_strategy and right_strategy and left_strategy == right_strategy)


def _exact_relation_status(candidate: Any, existing: Any) -> str:
    """Separate reprocessing from a new source version deterministically."""

    candidate_versions = _version_ids(candidate)
    existing_versions = _version_ids(existing)
    if candidate_versions and existing_versions:
        if candidate_versions & existing_versions:
            return "duplicate"
        return "new_evidence"
    if _same_record(candidate, existing):
        return "duplicate"
    # Two exact candidates without version provenance are distinct candidate
    # observations, so the safe relation is new evidence rather than silently
    # declaring the source record a duplicate.
    return "new_evidence"


def _candidate_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in ("candidates", "items", "related_candidates"):
            nested = value.get(key)
            if nested is not None:
                return _candidate_records(nested)
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _related_id_set(records: Iterable[Any]) -> tuple[str, ...]:
    return tuple(sorted({_record_id(record) for record in records if _record_id(record)}))


def _store(candidate: Any, field_name: str, value: Any) -> None:
    if isinstance(candidate, Mapping):
        try:
            candidate[field_name] = value  # type: ignore[index]
        except TypeError as exc:
            raise TypeError("candidate mapping must be mutable") from exc
        return
    try:
        setattr(candidate, field_name, value)
    except (AttributeError, TypeError) as exc:
        raise TypeError(f"candidate field {field_name!r} is not writable") from exc


def classify_novelty(
    candidate: Any,
    existing_candidates: Sequence[Any] | Iterable[Any] | None = None,
    *,
    persist: bool = False,
) -> NoveltyResult:
    """Classify one candidate against existing candidates.

    Rules, in descending strength:

    * exact canonical name/profile/core fields: ``duplicate`` when the source
      version is already linked, otherwise ``new_evidence``;
    * same canonical name and same profile with changed core fields:
      ``changed``;
    * overlapping family, asset, horizon, and core fields: ``variant``;
    * no deterministic relation: ``new``.

    ``document_version_ids`` is used only to distinguish a repeated exact
    record from an exact record supported by a new source version.  It is not
    used to search for or score similarity.
    """

    candidate = _unwrap_candidate(candidate)
    comparisons = _candidate_records(existing_candidates)
    comparison_groups: dict[str, list[Any]] = {
        "duplicate": [],
        "new_evidence": [],
        "changed": [],
        "variant": [],
    }

    for raw_existing in comparisons:
        existing = _unwrap_candidate(raw_existing)
        name_same = _same_canonical_name(candidate, existing)
        profile_same = _same_profile(candidate, existing)
        profile_related = _profile_related(candidate, existing)
        similarity = core_field_similarity(candidate, existing)

        if name_same and profile_same and similarity == 1.0:
            comparison_groups[_exact_relation_status(candidate, existing)].append(existing)
        elif name_same and profile_same:
            comparison_groups["changed"].append(existing)
        elif similarity >= _CORE_RELATED_THRESHOLD and (
            profile_related or name_same
        ):
            comparison_groups["variant"].append(existing)
        elif (
            similarity >= _CORE_CHANGED_THRESHOLD
            and name_same
            and profile_related
        ):
            comparison_groups["changed"].append(existing)

    if comparison_groups["duplicate"]:
        selected_status = "duplicate"
        selected_records = (
            comparison_groups["duplicate"] + comparison_groups["new_evidence"]
        )
    elif comparison_groups["new_evidence"]:
        selected_status = "new_evidence"
        selected_records = comparison_groups["new_evidence"]
    elif comparison_groups["changed"]:
        selected_status = "changed"
        selected_records = comparison_groups["changed"]
    elif comparison_groups["variant"]:
        selected_status = "variant"
        selected_records = comparison_groups["variant"]
    else:
        selected_status = "new"
        selected_records = []

    result = NoveltyResult(
        novelty_status=selected_status,
        related_strategy_ids=_related_id_set(selected_records),
    )
    if persist:
        _store(candidate, "novelty_status", result.novelty_status)
        _store(candidate, "related_strategy_ids", list(result.related_strategy_ids))
    return result


def apply_novelty(
    candidate: Any,
    existing_candidates: Sequence[Any] | Iterable[Any] | None = None,
) -> Any:
    """Persist the existing novelty fields and return the candidate object."""

    classify_novelty(candidate, existing_candidates, persist=True)
    return candidate


def set_novelty(
    candidate: Any,
    existing_candidates: Sequence[Any] | Iterable[Any] | None = None,
) -> Any:
    """Compatibility alias for ``apply_novelty``."""

    return apply_novelty(candidate, existing_candidates)


assess_novelty = classify_novelty
calculate_novelty = classify_novelty
determine_novelty = classify_novelty
route_novelty = classify_novelty
novelty = classify_novelty


__all__ = [
    "CORE_FIELDS",
    "NOVELTY_STATUSES",
    "NoveltyResult",
    "apply_novelty",
    "assess_novelty",
    "calculate_novelty",
    "classify_novelty",
    "core_field_similarity",
    "determine_novelty",
    "novelty",
    "route_novelty",
    "set_novelty",
]
