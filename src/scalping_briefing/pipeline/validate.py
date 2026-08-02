"""Validation boundary between extracted candidates and later pipeline work.

The validation step is intentionally narrower than publication.  It checks the
candidate schema and the Evidence contract, records the result on the input
document version, and exposes which core fields have usable Evidence.  It does
not build a candidate view, call the publication gate, or lint publication
phrases.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scalping_briefing.config import load_config
from scalping_briefing.llm.schema_guard import (
    SchemaValidationError,
    validate_strategy_candidate as validate_candidate_schema,
)
from scalping_briefing.models import Evidence
from scalping_briefing.models.base import new_id, utc_now
from scalping_briefing.pipeline import state_machine


CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)
CORE_FIELD_STATUS = {name: f"{name}_status" for name in CORE_FIELDS}
EVIDENCE_QUOTE_MAX_CHARS = 300
EVIDENCE_CONTAINER_KEYS = frozenset(
    {"accepted_evidence", "accepted_quotes", "evidence", "items", "quotes", "records"}
)


class CandidateValidationError(ValueError):
    """Base error for failures at the extracted-candidate boundary."""

    error_class = "candidate_validation_failed"


class EvidenceValidationError(CandidateValidationError):
    """Raised when an Evidence record cannot be trusted for this candidate."""

    error_class = "evidence_contract_failed"


@dataclass(slots=True)
class ValidationResult:
    """Outcome of validating one extracted candidate.

    ``candidate`` is returned only after both validation contracts pass.  A
    candidate with partial Evidence may still be structurally valid, but only
    fields in ``publishable_fields`` are safe for a later publication layer.
    """

    candidate: Any | None
    evidence: list[Evidence]
    processing_status: str
    document_version: Any
    publishable_fields: tuple[str, ...] = ()
    excluded_fields: tuple[str, ...] = CORE_FIELDS
    error_class: str | None = None
    error: str | None = None
    validated_payload: dict[str, Any] | None = None

    @property
    def valid(self) -> bool:
        return self.error_class is None and self.candidate is not None

    @property
    def success(self) -> bool:
        return self.valid

    @property
    def state(self) -> str:
        return self.processing_status

    @property
    def strategy_candidate(self) -> Any | None:
        return self.candidate

    @property
    def publishable(self) -> bool:
        """Whether every core field has a non-conflicting, linked value."""

        return self.valid and not self.excluded_fields

    @property
    def is_publishable(self) -> bool:
        return self.publishable

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": _field(self.candidate, "candidate_id"),
            "processing_status": self.processing_status,
            "evidence_count": len(self.evidence),
            "publishable": self.publishable,
            "publishable_fields": list(self.publishable_fields),
            "excluded_fields": list(self.excluded_fields),
            "error_class": self.error_class,
            "error": self.error,
        }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _state(value: Any) -> str:
    current = _field(value, "processing_status")
    if current is None:
        current = _field(value, "state")
    if current is None:
        raise CandidateValidationError("document version processing_status is required")
    return str(getattr(current, "value", current))


def _set_state(value: Any, target: str) -> None:
    current = _state(value)
    if current == target:
        return
    state_machine.transition(current, target)
    if isinstance(value, Mapping):
        try:
            value["processing_status"] = target  # type: ignore[index]
        except TypeError as exc:
            raise TypeError("document version mapping must be mutable") from exc
    else:
        setattr(value, "processing_status", target)


def _metadata(value: Any) -> dict[str, Any]:
    metadata = _field(value, "metadata_json")
    if metadata is None:
        metadata = _field(value, "metadata", {})
    return dict(metadata) if isinstance(metadata, Mapping) else {}


def _store_metadata(value: Any, metadata: Mapping[str, Any]) -> None:
    payload = dict(metadata)
    if isinstance(value, Mapping):
        value["metadata"] = payload  # type: ignore[index]
        if "metadata_json" in value:  # type: ignore[operator]
            value["metadata_json"] = payload  # type: ignore[index]
        return
    setattr(value, "metadata_json", payload)


def _version_id(value: Any) -> str:
    version_id = _field(value, "document_version_id")
    if version_id is None or not str(version_id).strip():
        raise EvidenceValidationError("document_version_id is required")
    return str(version_id).strip()


def _candidate_id(value: Any) -> str:
    candidate_id = _field(value, "candidate_id")
    if candidate_id is None or not str(candidate_id).strip():
        raise CandidateValidationError("candidate_id is required")
    return str(candidate_id).strip()


def _document_url(value: Any) -> str | None:
    url = _field(value, "canonical_url")
    if url is None:
        document = _field(value, "document")
        url = _field(document, "canonical_url") if document is not None else None
    return str(url) if url else None


def _document_text(value: Any) -> str | None:
    for name in ("normalized_text", "normalized_body", "text", "body", "content"):
        text = _field(value, name)
        if isinstance(text, str):
            return text
    location = _field(value, "normalized_location")
    if location:
        try:
            return Path(location).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
    return None


def _compact(value: str) -> str:
    return " ".join(value.split())


def _quote_in_source(quote: str, source_text: str | None) -> bool:
    if not isinstance(source_text, str) or not source_text.strip():
        raise EvidenceValidationError("normalized source text is required for Evidence")
    return quote in source_text or _compact(quote) in _compact(source_text)


def _quote_limit(value: int | None) -> int:
    selected = load_config().quote_max_chars if value is None else value
    try:
        limit = int(selected)
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("quote_max_chars must be an integer") from exc
    if limit < 1:
        raise EvidenceValidationError("quote_max_chars must be positive")
    if limit > EVIDENCE_QUOTE_MAX_CHARS:
        raise EvidenceValidationError(
            f"quote_max_chars must not exceed {EVIDENCE_QUOTE_MAX_CHARS}"
        )
    return limit


def _normalise_evidence(value: Any) -> list[dict[str, Any]]:
    return _normalise_evidence_value(value)


def _normalise_evidence_value(
    value: Any,
    *,
    default_field: str | None = None,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "field_name" in value or "quote" in value:
            item = dict(value)
            if default_field is not None:
                item.setdefault("field_name", default_field)
            return [item]
        result: list[dict[str, Any]] = []
        for field_name, nested in value.items():
            if field_name in EVIDENCE_CONTAINER_KEYS:
                result.extend(
                    _normalise_evidence_value(nested, default_field=default_field)
                )
                continue
            result.extend(
                _normalise_evidence_value(nested, default_field=str(field_name))
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for entry in value:
            if isinstance(entry, Mapping):
                item = dict(entry)
                if default_field is not None:
                    item.setdefault("field_name", default_field)
                result.append(item)
            elif isinstance(entry, str) and default_field is not None:
                result.append({"field_name": default_field, "quote": entry})
            else:
                item = _evidence_mapping(entry)
                if default_field is not None:
                    item.setdefault("field_name", default_field)
                result.append(item)
        return result
    if isinstance(value, str):
        if default_field is None:
            raise EvidenceValidationError("Evidence field_name is required")
        return [{"field_name": default_field, "quote": value}]
    item = _evidence_mapping(value)
    if default_field is not None:
        item.setdefault("field_name", default_field)
    return [item]


def _evidence_mapping(value: Any) -> dict[str, Any]:
    fields = (
        "evidence_id",
        "document_version_id",
        "strategy_candidate_id",
        "field_name",
        "quote",
        "section_or_locator",
        "captured_at",
        "source_url",
        "metadata_json",
        "metadata",
    )
    result = {name: _field(value, name) for name in fields}
    if result.get("metadata") is None and result.get("metadata_json") is not None:
        result["metadata"] = result["metadata_json"]
    return {key: item for key, item in result.items() if item is not None}


def _payload_evidence(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    return _normalise_evidence(metadata.get("evidence"))


def _candidate_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    properties = (
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
    )
    required_properties = {
        "candidate_id",
        "canonical_name",
        "summary",
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
        "risk_notes",
        "risk_notes_status",
        "field_status",
        "relevance_status",
        "review_status",
        "source_confidence",
        "extraction_confidence",
    }
    result: dict[str, Any] = {}
    for name in properties:
        value_for_property = _field(value, name)
        if name in required_properties or value_for_property is not None:
            result[name] = copy.deepcopy(value_for_property)
    return result


def _normalise_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError as exc:
            raise EvidenceValidationError(
                "Evidence captured_at must be an ISO datetime"
            ) from exc
    if value is None:
        return utc_now()
    raise EvidenceValidationError("Evidence captured_at must be a datetime")


def _evidence_metadata(entry: Mapping[str, Any]) -> dict[str, Any]:
    reserved = {
        "accepted",
        "accepted_by_extraction",
        "captured_at",
        "document_version_id",
        "evidence_id",
        "field_name",
        "linkable",
        "metadata",
        "metadata_json",
        "quote",
        "quote_verified",
        "section_or_locator",
        "source_url",
        "source_verified",
        "status",
        "strategy_candidate_id",
        "verified",
    }
    metadata: dict[str, Any] = {}
    nested = entry.get("metadata")
    if nested is None:
        nested = entry.get("metadata_json")
    if nested is not None:
        if not isinstance(nested, Mapping):
            raise EvidenceValidationError("Evidence metadata must be an object")
        metadata.update(dict(nested))
    metadata.update(
        {
            key: item
            for key, item in entry.items()
            if key not in reserved
        }
    )
    return metadata


def _validated_evidence(
    value: Any,
    *,
    document_version: Any,
    candidate_id: str,
    document_text: str | None,
    quote_max_chars: int | None,
) -> list[Evidence]:
    entries = _normalise_evidence(value)
    if not entries:
        return []
    version_id = _version_id(document_version)
    quote_limit = _quote_limit(quote_max_chars)
    field_counts: dict[str, int] = {}
    rows: list[Evidence] = []

    for entry in entries:
        field_name = entry.get("field_name")
        quote = entry.get("quote")
        if not isinstance(field_name, str) or not field_name.strip():
            raise EvidenceValidationError("Evidence field_name is required")
        field_name = field_name.strip()
        if field_name not in CORE_FIELDS:
            raise EvidenceValidationError(
                f"unsupported Evidence field {field_name!r}"
            )
        if not isinstance(quote, str) or not quote.strip():
            raise EvidenceValidationError("Evidence quote is required")
        quote = quote.strip()
        if len(quote) > quote_limit:
            raise EvidenceValidationError(
                f"Evidence quote exceeds quote_max_chars ({quote_limit})"
            )
        if not _quote_in_source(quote, document_text):
            raise EvidenceValidationError(
                f"Evidence quote for {field_name!r} is not present in source"
            )

        evidence_version = entry.get("document_version_id", version_id)
        if str(evidence_version) != version_id:
            raise EvidenceValidationError(
                "Evidence must point to the input document_version_id"
            )
        evidence_candidate = entry.get("strategy_candidate_id", candidate_id)
        if str(evidence_candidate) != candidate_id:
            raise EvidenceValidationError(
                "Evidence must point to the input strategy_candidate_id"
            )
        locator = entry.get("section_or_locator")
        if not isinstance(locator, str) or not locator.strip():
            raise EvidenceValidationError("Evidence section_or_locator is required")

        field_counts[field_name] = field_counts.get(field_name, 0) + 1
        if field_counts[field_name] > 2:
            raise EvidenceValidationError(
                f"Evidence count exceeds two quotes for field {field_name!r}"
            )

        rows.append(
            Evidence(
                evidence_id=str(entry.get("evidence_id") or new_id()),
                document_version_id=version_id,
                strategy_candidate_id=candidate_id,
                field_name=field_name,
                quote=quote,
                section_or_locator=locator.strip(),
                captured_at=_normalise_datetime(entry.get("captured_at")),
                source_url=str(
                    entry.get("source_url") or _document_url(document_version) or ""
                )
                or None,
                metadata=_evidence_metadata(entry),
            )
        )
    return rows


def _publishable_fields(payload: Mapping[str, Any], evidence: Sequence[Evidence]) -> tuple[str, ...]:
    evidence_fields = {row.field_name for row in evidence}
    field_status = payload.get("field_status")
    field_status = field_status if isinstance(field_status, Mapping) else {}
    result: list[str] = []
    for field_name in CORE_FIELDS:
        value = payload.get(field_name)
        status = payload.get(CORE_FIELD_STATUS[field_name])
        has_value = bool(value) if isinstance(value, (str, list, tuple, dict)) else value is not None
        if (
            field_name in evidence_fields
            and has_value
            and status in {"explicit", "inferred"}
            and field_status.get(field_name) not in {"unknown", "conflicting", "not_applicable"}
        ):
            result.append(field_name)
    return tuple(result)


def _record_failure(
    document_version: Any,
    *,
    error_class: str,
    error: Exception,
) -> None:
    current = _state(document_version)
    if current == "extracted":
        _set_state(document_version, "failed")
    metadata = _metadata(document_version)
    metadata["validation"] = {
        "status": "failed",
        "error_class": error_class,
        "error": str(error),
    }
    metadata["error_class"] = error_class
    _store_metadata(document_version, metadata)


def _record_success(
    document_version: Any,
    *,
    candidate_id: str,
    evidence_count: int,
    publishable_fields: Sequence[str],
    excluded_fields: Sequence[str],
) -> None:
    _set_state(document_version, "validated")
    metadata = _metadata(document_version)
    metadata["validation"] = {
        "status": "validated",
        "candidate_id": candidate_id,
        "evidence_count": evidence_count,
        "publishable_fields": list(publishable_fields),
        "excluded_fields": list(excluded_fields),
    }
    _store_metadata(document_version, metadata)


def _unpack_inputs(
    extracted: Any,
    positional: Sequence[Any],
    *,
    document_version: Any,
    candidate: Any,
    evidence: Any,
) -> tuple[Any, Any, Any]:
    """Accept the three natural adapter shapes without adding a new state."""

    remaining = list(positional)
    if hasattr(extracted, "candidate") and hasattr(extracted, "document_version"):
        result = extracted
        if document_version is None:
            document_version = _field(result, "document_version")
        candidate = candidate if candidate is not None else _field(result, "candidate")
        if evidence is None:
            evidence = _field(result, "evidence")
        if candidate is None:
            candidate = _field(result, "validated_payload")
        if remaining:
            raise TypeError("unexpected positional validation arguments")
        return document_version, candidate, evidence

    extracted_is_version = (
        _field(extracted, "processing_status") is not None
        or _field(extracted, "state") is not None
    )
    if document_version is None and extracted_is_version:
        document_version = extracted
        if candidate is None and remaining:
            candidate = remaining.pop(0)
        if evidence is None and remaining:
            evidence = remaining.pop(0)
    elif candidate is None:
        candidate = extracted
        if evidence is None and remaining:
            evidence = remaining.pop(0)

    if remaining:
        raise TypeError("unexpected positional validation arguments")
    if document_version is None:
        raise TypeError("document_version is required")
    return document_version, candidate, evidence


def validate_extracted_candidate(
    extracted: Any,
    *positional: Any,
    document_version: Any = None,
    candidate: Any = None,
    evidence: Any = None,
    schema_path: str | Path | None = None,
    document_text: str | None = None,
    quote_max_chars: int | None = None,
) -> ValidationResult:
    """Validate an extracted candidate and close its state transition.

    Supported calls include ``validate_extracted_candidate(version, payload,
    evidence)``, ``validate_extracted_candidate(payload,
    document_version=version, evidence=evidence)``, and passing an
    ``ExtractionResult`` directly.  No candidate or Evidence row is persisted
    by this function; callers receive a result suitable for a later stage.
    """

    document_version, candidate, evidence = _unpack_inputs(
        extracted,
        positional,
        document_version=document_version,
        candidate=candidate,
        evidence=evidence,
    )
    current = _state(document_version)
    if current != "extracted":
        raise state_machine.InvalidTransition(
            f"candidate validation requires extracted state, got {current!r}"
        )

    payload: dict[str, Any] | None = None
    candidate_value: Any | None = None
    failure: Exception | None = None
    try:
        if candidate is None:
            raise CandidateValidationError("extracted candidate is required")
        payload = _candidate_payload(candidate)
        validated_payload = validate_candidate_schema(
            payload,
            schema_path=schema_path,
        )
        candidate_id = _candidate_id(validated_payload)
        version_id = _version_id(document_version)
        candidate_version_ids = validated_payload.get("document_version_ids")
        if candidate_version_ids is not None and version_id not in candidate_version_ids:
            raise CandidateValidationError(
                "candidate document_version_ids do not include input version"
            )
        if evidence is None:
            evidence = _field(candidate, "evidence")
            if evidence is None:
                evidence = _payload_evidence(validated_payload)
        elif isinstance(evidence, Mapping) and not evidence:
            evidence = []
        evidence_rows = _validated_evidence(
            evidence,
            document_version=document_version,
            candidate_id=candidate_id,
            document_text=(
                document_text
                if document_text is not None
                else _document_text(document_version)
            ),
            quote_max_chars=quote_max_chars,
        )
        publishable_fields = _publishable_fields(validated_payload, evidence_rows)
        excluded_fields = tuple(
            field_name
            for field_name in CORE_FIELDS
            if field_name not in publishable_fields
        )
        _record_success(
            document_version,
            candidate_id=candidate_id,
            evidence_count=len(evidence_rows),
            publishable_fields=publishable_fields,
            excluded_fields=excluded_fields,
        )
        safe_payload = copy.deepcopy(validated_payload)
        safe_metadata = safe_payload.get("metadata")
        if isinstance(safe_metadata, Mapping):
            safe_metadata = dict(safe_metadata)
            safe_metadata.pop("evidence", None)
            safe_payload["metadata"] = safe_metadata
        candidate_value = candidate if not isinstance(candidate, Mapping) else safe_payload
        return ValidationResult(
            candidate=candidate_value,
            evidence=evidence_rows,
            processing_status=_state(document_version),
            document_version=document_version,
            publishable_fields=publishable_fields,
            excluded_fields=excluded_fields,
            validated_payload=safe_payload,
        )
    except SchemaValidationError as exc:
        failure = exc
        error_class = getattr(exc, "error_class", "schema_validation_error")
    except CandidateValidationError as exc:
        failure = exc
        error_class = getattr(exc, "error_class", "candidate_validation_failed")
    except Exception as exc:
        failure = exc
        error_class = getattr(exc, "error_class", "candidate_validation_failed")

    assert failure is not None
    _record_failure(
        document_version,
        error_class=error_class,
        error=failure,
    )
    return ValidationResult(
        candidate=None,
        evidence=[],
        processing_status=_state(document_version),
        document_version=document_version,
        publishable_fields=(),
        excluded_fields=CORE_FIELDS,
        error_class=error_class,
        error=str(failure),
        validated_payload=None,
    )


def validate_candidate(*args: Any, **kwargs: Any) -> ValidationResult:
    """Compatibility alias for candidate-oriented pipeline callers."""

    return validate_extracted_candidate(*args, **kwargs)


def validate_strategy_candidate(*args: Any, **kwargs: Any) -> ValidationResult:
    """Candidate-named alias that keeps schema validation in its own module."""

    return validate_extracted_candidate(*args, **kwargs)


def validate_extraction(*args: Any, **kwargs: Any) -> ValidationResult:
    """Compatibility alias for extraction-result-oriented callers."""

    return validate_extracted_candidate(*args, **kwargs)


def validate_document_version(*args: Any, **kwargs: Any) -> ValidationResult:
    """Document-version-named alias matching the classification adapter."""

    return validate_extracted_candidate(*args, **kwargs)


validate = validate_extracted_candidate


__all__ = [
    "CORE_FIELDS",
    "CandidateValidationError",
    "EvidenceValidationError",
    "SchemaValidationError",
    "ValidationResult",
    "validate_candidate",
    "validate",
    "validate_document_version",
    "validate_extracted_candidate",
    "validate_extraction",
    "validate_strategy_candidate",
]
