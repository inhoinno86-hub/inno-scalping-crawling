"""Adapt validated strategy candidates to the existing publication contract.

The publication gate owns all renderer-facing safety checks.  This module only
organises one candidate's core fields and their Evidence into separate
briefing items so the gate's per-item limits and phrase lint remain effective.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from scalping_briefing.pipeline.validate import CORE_FIELDS

from . import gate


_MISSING = object()
_CANDIDATE_FIELDS = (
    "candidate_id",
    "strategy_id",
    "canonical_name",
    "aliases",
    "summary",
    "asset_classes",
    "market_types",
    "strategy_families",
    "holding_horizon",
    "microstructure_level",
    "tags",
    "core_hypothesis",
    "core_hypothesis_status",
    "signal_inputs",
    "signal_inputs_status",
    "entry_logic",
    "entry_logic_status",
    "exit_logic",
    "exit_logic_status",
    "required_data",
    "required_data_status",
    "required_frequency",
    "risk_notes",
    "risk_notes_status",
    "field_status",
    "relevance_status",
    "review_status",
    "source_confidence",
    "extraction_confidence",
    "value_score",
    "value_score_breakdown",
    "novelty_status",
    "related_strategy_ids",
    "document_version_ids",
    "metadata",
    "source_url",
    "original_url",
    "canonical_url",
)
_EVIDENCE_FIELDS = (
    "evidence_id",
    "document_version_id",
    "strategy_candidate_id",
    "field_name",
    "quote",
    "section_or_locator",
    "captured_at",
    "source_url",
    "original_url",
    "source_link",
    "original_source_url",
    "original_source_link",
    "original_link",
    "metadata",
    "metadata_json",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        return deepcopy(dict(candidate))

    result: dict[str, Any] = {}
    for name in _CANDIDATE_FIELDS:
        value = _field(candidate, name, _MISSING)
        if value is not _MISSING:
            result[name] = deepcopy(value)
    return result


def _record_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "field_name" in value or "quote" in value:
            return [value]
        records: list[Any] = []
        for field_name, nested in value.items():
            for record in _record_values(nested):
                if isinstance(record, Mapping):
                    item = dict(record)
                    item.setdefault("field_name", str(field_name))
                    records.append(item)
                else:
                    records.append(record)
        return records
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _evidence_mapping(record: Any) -> dict[str, Any]:
    if isinstance(record, Mapping):
        return deepcopy(dict(record))

    result: dict[str, Any] = {}
    for name in _EVIDENCE_FIELDS:
        value = _field(record, name, _MISSING)
        if value is not _MISSING:
            result[name] = deepcopy(value)
    if "metadata" not in result and "metadata_json" in result:
        result["metadata"] = result["metadata_json"]
    return result


def _source_link(candidate: Mapping[str, Any]) -> str | None:
    for name in ("source_url", "original_url", "canonical_url"):
        value = candidate.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _candidate_result_parts(candidate: Any, evidence: Any) -> tuple[Any, Any]:
    """Support passing a ValidationResult without creating a new contract."""

    inner_candidate = _field(candidate, "candidate", _MISSING)
    if inner_candidate is not _MISSING and inner_candidate is not None:
        if evidence is None:
            evidence = _field(candidate, "evidence", None)
        candidate = inner_candidate
    return candidate, evidence


def build_candidate_view(candidate: Any, evidence: Any = None) -> dict[str, Any]:
    """Build and gate a renderer-facing view for one strategy candidate.

    Each core candidate field becomes one briefing item.  That preserves the
    existing gate's ``MAX_EVIDENCE_QUOTES`` contract per field while ensuring a
    field with no Evidence reaches the gate as an empty item and is rejected.
    The input candidate and Evidence records are never mutated.
    """

    candidate, evidence = _candidate_result_parts(candidate, evidence)
    payload = _candidate_payload(candidate)
    candidate_id = payload.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise gate.PublicationGateError("candidate_id is required for publication")
    candidate_id = candidate_id.strip()

    payload.pop("evidence", None)
    payload["candidate_id"] = candidate_id
    payload["strategy_candidate_id"] = candidate_id
    fallback_link = _source_link(payload)

    grouped: dict[str, list[dict[str, Any]]] = {field: [] for field in CORE_FIELDS}
    for record in _record_values(evidence):
        mapped = _evidence_mapping(record)
        field_name = mapped.get("field_name")
        if not isinstance(field_name, str) or not field_name.strip():
            raise gate.PublicationGateError("Evidence field_name is required")
        field_name = field_name.strip()
        if field_name not in grouped:
            raise gate.PublicationGateError(
                f"unsupported Evidence field {field_name!r}"
            )
        mapped["field_name"] = field_name
        if not mapped.get("source_url") and fallback_link:
            mapped["source_url"] = fallback_link
        grouped[field_name].append(mapped)

    items: list[dict[str, Any]] = []
    for field_name in CORE_FIELDS:
        item = deepcopy(payload)
        item["briefing_item_id"] = f"{candidate_id}:{field_name}"
        item["field_name"] = field_name
        item["claim"] = deepcopy(payload.get(field_name))
        item["evidence"] = grouped[field_name]
        items.append(item)

    publication = {"items": items}
    gate.validate_publication(publication)
    return publication


def candidate_view(candidate: Any, evidence: Any = None) -> dict[str, Any]:
    """Compatibility spelling for callers that use the module name as an API."""

    return build_candidate_view(candidate, evidence)


to_publication_input = build_candidate_view
validate_candidate_view = build_candidate_view


CandidateViewError = gate.PublicationGateError


__all__ = [
    "CandidateViewError",
    "build_candidate_view",
    "candidate_view",
    "to_publication_input",
    "validate_candidate_view",
]
