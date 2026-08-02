"""Schema-guarded strategy candidate extraction for Phase 2."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from scalping_briefing.config import load_config
from scalping_briefing.llm.audit import LLMCallResult, audited_complete
from scalping_briefing.llm.fixture import FixtureLLMClient
from scalping_briefing.llm.prompts import (
    EXTRACTION_PROMPT_VERSION,
    build_extraction_prompt,
)
from scalping_briefing.llm.schema_guard import (
    SchemaValidationError,
    validate_strategy_candidate,
)
from scalping_briefing.models import Evidence, StrategyCandidate
from scalping_briefing.models.base import new_id, utc_now
from scalping_briefing.pipeline import state_machine

from .classify import _metadata, _set_state, _state, _store_metadata


CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)
CORE_FIELD_STATUS = {field: f"{field}_status" for field in CORE_FIELDS}


class CandidateMappingError(ValueError):
    """Raised when a validated payload cannot be safely persisted."""

    error_class = "candidate_mapping_failed"


class EvidenceContractError(CandidateMappingError):
    """Raised when an evidence record cannot prove its document-version link."""

    error_class = "evidence_contract_failed"


@dataclass(slots=True)
class ExtractionResult:
    """Candidate extraction outcome, including failure-path audit metadata."""

    candidate: StrategyCandidate | None
    processing_status: str
    document_version: Any
    raw_response: Any = None
    validated_payload: dict[str, Any] | None = None
    prompt: str | None = None
    prompt_hash: str | None = None
    llm_run: Any = None
    evidence: list[Evidence] | None = None
    error_class: str | None = None
    error: str | None = None

    @property
    def strategy_candidate(self) -> StrategyCandidate | None:
        return self.candidate

    @property
    def state(self) -> str:
        return self.processing_status

    @property
    def success(self) -> bool:
        return self.candidate is not None and self.error_class is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": getattr(self.candidate, "candidate_id", None),
            "processing_status": self.processing_status,
            "prompt_hash": self.prompt_hash,
            "llm_run_id": getattr(self.llm_run, "llm_run_id", None),
            "evidence_count": len(self.evidence or []),
            "error_class": self.error_class,
            "error": self.error,
        }


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _version_id(document_version: Any) -> str | None:
    value = _field(document_version, "document_version_id")
    return str(value) if value is not None else None


def _document_url(document_version: Any) -> str | None:
    value = _field(document_version, "canonical_url")
    if value is None:
        document = _field(document_version, "document")
        value = _field(document, "canonical_url") if document is not None else None
    return str(value) if value else None


def _document_text(document_version: Any, explicit: str | None) -> str | None:
    if explicit is not None:
        return explicit
    for name in ("normalized_text", "normalized_body", "text", "body", "content"):
        value = _field(document_version, name)
        if isinstance(value, str):
            return value
    location = _field(document_version, "normalized_location")
    if location:
        try:
            from pathlib import Path

            return Path(location).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
    return None


def _classification(document_version: Any, explicit: Mapping[str, Any] | None) -> dict[str, Any]:
    if explicit is not None:
        return dict(explicit)
    value = _metadata(document_version).get("classification", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _normalise_evidence_input(
    value: Any,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if "field_name" in value or "quote" in value:
            return [dict(value)]
        entries: list[dict[str, Any]] = []
        for field_name, field_evidence in value.items():
            if isinstance(field_evidence, Mapping):
                item = dict(field_evidence)
                item.setdefault("field_name", str(field_name))
                entries.append(item)
            elif isinstance(field_evidence, Sequence) and not isinstance(
                field_evidence, (str, bytes, bytearray)
            ):
                for entry in field_evidence:
                    if isinstance(entry, Mapping):
                        item = dict(entry)
                        item.setdefault("field_name", str(field_name))
                        entries.append(item)
        return entries
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [dict(entry) for entry in value if isinstance(entry, Mapping)]
    raise EvidenceContractError("evidence must be a mapping or list of mappings")


def _payload_evidence(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        return []
    return _normalise_evidence_input(metadata.get("evidence"))


def _safe_unknown_value(field: str) -> None:
    # ``None`` is the schema-level unknown representation for both nullable
    # text and nullable text-list fields.
    return None


def _quote_limit(value: int | None = None) -> int:
    """Read the existing quote limit instead of duplicating configuration."""

    selected = load_config().quote_max_chars if value is None else value
    try:
        limit = int(selected)
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError("quote_max_chars must be an integer") from exc
    if limit < 0:
        raise EvidenceContractError("quote_max_chars must not be negative")
    return limit


def _compact_text(value: str) -> str:
    return " ".join(value.split())


def _quote_in_normalized_body(quote: str, normalized_text: str | None) -> bool:
    """Require Evidence quotes to be reproducible from normalized source text."""

    if not isinstance(normalized_text, str) or not normalized_text.strip():
        return False
    return quote in normalized_text or _compact_text(quote) in _compact_text(normalized_text)


def _safe_candidate_payload(
    validated_payload: Mapping[str, Any],
    evidence_entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Keep unsupported core claims out of the stored candidate.

    This transformation runs only after the raw response has passed the
    repository JSON Schema.  It does not invent values; it removes unsupported
    values and labels them ``unknown``.
    """

    payload = copy.deepcopy(dict(validated_payload))
    field_status = payload.get("field_status")
    statuses = dict(field_status) if isinstance(field_status, Mapping) else {}
    by_field: dict[str, list[Mapping[str, Any]]] = {}
    for item in evidence_entries:
        field_name = item.get("field_name")
        if isinstance(field_name, str) and field_name:
            by_field.setdefault(field_name.strip(), []).append(item)

    requires_review = False
    for field in CORE_FIELDS:
        status_field = CORE_FIELD_STATUS[field]
        raw_status = payload.get(status_field)
        entries = by_field.get(field, [])
        explicit_conflict = bool(entries) and (
            raw_status == "conflicting"
            or any(
                item.get("status") == "conflicting"
                or item.get("conflicting") is True
                for item in entries
            )
        )
        if explicit_conflict:
            payload[status_field] = "conflicting"
            statuses[field] = "conflicting"
        elif not entries:
            payload[field] = _safe_unknown_value(field)
            payload[status_field] = "unknown"
            statuses[field] = "unknown"
            requires_review = True
        else:
            statuses[field] = raw_status or "unknown"

    payload["field_status"] = statuses
    if requires_review and payload.get("review_status") in {"pending", "approved"}:
        payload["review_status"] = "needs_review"
    metadata = payload.get("metadata")
    if isinstance(metadata, Mapping):
        clean_metadata = dict(metadata)
        # Evidence is persisted through Evidence rows after its own contract
        # validation, never as an unvalidated nested candidate blob.
        clean_metadata.pop("evidence", None)
        payload["metadata"] = clean_metadata
    return payload


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None


def _accepted_evidence_entries(
    evidence_entries: Sequence[Mapping[str, Any]],
    *,
    document_version: Any,
    normalized_text: str | None,
    quote_max_chars: int | None = None,
) -> list[dict[str, Any]]:
    version_id = _version_id(document_version)
    if not evidence_entries:
        return []
    quote_limit = _quote_limit(quote_max_chars)
    grouped: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    for item in evidence_entries:
        field_name = item.get("field_name")
        quote = item.get("quote")
        item_version_id = item.get("document_version_id", version_id)
        if not isinstance(field_name, str) or not field_name.strip():
            raise EvidenceContractError("evidence field_name is required")
        if not isinstance(quote, str) or not quote.strip():
            raise EvidenceContractError("evidence quote is required")
        field_name = field_name.strip()
        quote = quote.strip()
        if len(quote) > quote_limit:
            raise EvidenceContractError(
                f"evidence quote exceeds quote_max_chars ({quote_limit})"
            )
        if version_id is None or str(item_version_id) != version_id:
            raise EvidenceContractError(
                "evidence must point to the input document_version_id"
            )
        grouped[field_name] = grouped.get(field_name, 0) + 1
        if grouped[field_name] > 2:
            raise EvidenceContractError(
                f"evidence count exceeds two quotes for field {field_name!r}"
            )
        locator = item.get("section_or_locator")
        if not isinstance(locator, str) or not locator.strip():
            raise EvidenceContractError("evidence section_or_locator is required")
        # A syntactically valid quote is still untrusted until it can be found
        # in this exact document version.  Drop it; caller will downgrade the
        # unsupported core field to unknown/needs_review.
        if not _quote_in_normalized_body(quote, normalized_text):
            continue
        accepted_item = dict(item)
        accepted_item["field_name"] = field_name
        accepted_item["quote"] = quote
        accepted_item["section_or_locator"] = locator.strip()
        accepted.append(accepted_item)
    return accepted


def _validated_evidence(
    evidence_entries: Sequence[Mapping[str, Any]],
    *,
    document_version: Any,
    candidate_id: str,
    normalized_text: str | None,
    quote_max_chars: int | None = None,
) -> list[Evidence]:
    accepted_entries = _accepted_evidence_entries(
        evidence_entries,
        document_version=document_version,
        normalized_text=normalized_text,
        quote_max_chars=quote_max_chars,
    )
    version_id = _version_id(document_version)
    result: list[Evidence] = []
    for item in accepted_entries:
        metadata = {
            key: value
            for key, value in item.items()
            if key not in {
                "evidence_id",
                "document_version_id",
                "strategy_candidate_id",
                "field_name",
                "quote",
                "section_or_locator",
                "captured_at",
            }
        }
        result.append(
            Evidence(
                evidence_id=str(item.get("evidence_id") or new_id()),
                document_version_id=version_id,
                strategy_candidate_id=candidate_id,
                field_name=item["field_name"],
                quote=item["quote"],
                section_or_locator=item["section_or_locator"],
                captured_at=_parse_datetime(item.get("captured_at")) or utc_now(),
                source_url=str(item.get("source_url") or _document_url(document_version) or "")
                or None,
                metadata=metadata,
            )
        )
    return result


def candidate_from_validated(
    validated_payload: Mapping[str, Any],
    *,
    document_version: Any = None,
    evidence: Any = None,
    document_text: str | None = None,
    quote_max_chars: int | None = None,
) -> tuple[StrategyCandidate, list[Evidence]]:
    """Map a schema-valid payload without filling core fields from guesses."""

    payload = dict(validated_payload)
    candidate_id = str(payload["candidate_id"])
    version_id = _version_id(document_version)
    version_ids = payload.get("document_version_ids") or []
    if version_id is not None and version_id not in version_ids:
        raise CandidateMappingError(
            "candidate document_version_ids do not include input version"
        )
    explicit_evidence = _normalise_evidence_input(evidence)
    if evidence is None:
        explicit_evidence = _payload_evidence(payload)
    normalized_text = _document_text(document_version, document_text)
    accepted_evidence = _accepted_evidence_entries(
        explicit_evidence,
        document_version=document_version,
        normalized_text=normalized_text,
        quote_max_chars=quote_max_chars,
    )
    safe_payload = _safe_candidate_payload(payload, accepted_evidence)
    values: dict[str, Any] = {
        "candidate_id": candidate_id,
        "strategy_id": safe_payload.get("strategy_id"),
        "canonical_name": safe_payload["canonical_name"],
        "aliases": list(safe_payload.get("aliases") or []),
        "summary": safe_payload["summary"],
        "asset_classes": list(safe_payload.get("asset_classes") or []),
        "market_types": list(safe_payload.get("market_types") or []),
        "strategy_families": list(safe_payload.get("strategy_families") or []),
        "holding_horizon": safe_payload.get("holding_horizon"),
        "microstructure_level": safe_payload.get("microstructure_level"),
        "tags": list(safe_payload.get("tags") or []),
        "core_hypothesis": safe_payload.get("core_hypothesis"),
        "core_hypothesis_status": safe_payload["core_hypothesis_status"],
        "signal_inputs": safe_payload.get("signal_inputs"),
        "signal_inputs_status": safe_payload["signal_inputs_status"],
        "entry_logic": safe_payload.get("entry_logic"),
        "entry_logic_status": safe_payload["entry_logic_status"],
        "exit_logic": safe_payload.get("exit_logic"),
        "exit_logic_status": safe_payload["exit_logic_status"],
        "required_data": safe_payload.get("required_data"),
        "required_data_status": safe_payload["required_data_status"],
        "required_frequency": safe_payload.get("required_frequency"),
        "risk_notes": safe_payload.get("risk_notes"),
        "risk_notes_status": safe_payload["risk_notes_status"],
        "field_status": dict(safe_payload["field_status"]),
        "relevance_status": safe_payload["relevance_status"],
        "review_status": safe_payload["review_status"],
        "source_confidence": safe_payload.get("source_confidence"),
        "extraction_confidence": safe_payload.get("extraction_confidence"),
        "value_score": safe_payload.get("value_score"),
        "value_score_breakdown": dict(safe_payload.get("value_score_breakdown") or {}),
        "novelty_status": safe_payload.get("novelty_status"),
        "related_strategy_ids": list(safe_payload.get("related_strategy_ids") or []),
        "document_version_ids": list(version_ids),
        "metadata": dict(safe_payload.get("metadata") or {}),
    }
    candidate = StrategyCandidate(**values)
    evidence_rows = _validated_evidence(
        accepted_evidence,
        document_version=document_version,
        candidate_id=candidate_id,
        normalized_text=normalized_text,
        quote_max_chars=quote_max_chars,
    )
    return candidate, evidence_rows


def map_candidate(
    raw_output: Any,
    *,
    document_version: Any = None,
    evidence: Any = None,
    schema_path: str | None = None,
    document_text: str | None = None,
    quote_max_chars: int | None = None,
) -> tuple[StrategyCandidate, list[Evidence]]:
    """Validate raw output before creating any candidate or evidence object."""

    validated = validate_strategy_candidate(raw_output, schema_path=schema_path)
    return candidate_from_validated(
        validated,
        document_version=document_version,
        evidence=evidence,
        document_text=document_text,
        quote_max_chars=quote_max_chars,
    )


def _persist_candidate(
    session: Any,
    candidate: StrategyCandidate,
    evidence_rows: Sequence[Evidence],
) -> tuple[StrategyCandidate, list[Evidence]]:
    if session is None:
        return candidate, list(evidence_rows)
    existing = session.get(StrategyCandidate, candidate.candidate_id)
    if existing is not None:
        return existing, list(existing.evidence or [])
    session.add(candidate)
    session.flush()
    for evidence_row in evidence_rows:
        session.add(evidence_row)
    session.flush()
    return candidate, list(evidence_rows)


def _failure(
    document_version: Any,
    *,
    error_class: str,
    error: Exception,
    llm_run: Any,
    prompt: str | None,
    prompt_hash: str | None,
    raw_response: Any = None,
    validated_payload: dict[str, Any] | None = None,
) -> ExtractionResult:
    current = _state(document_version)
    if current == "extracted":
        _set_state(document_version, "failed")
    metadata = _metadata(document_version)
    metadata["extraction"] = {
        "status": "failed",
        "error_class": error_class,
        "error": str(error),
        "prompt_hash": prompt_hash,
        "llm_run_id": getattr(llm_run, "llm_run_id", None),
    }
    metadata["error_class"] = error_class
    _store_metadata(document_version, metadata)
    return ExtractionResult(
        candidate=None,
        processing_status=_state(document_version),
        document_version=document_version,
        raw_response=raw_response,
        validated_payload=validated_payload,
        prompt=prompt,
        prompt_hash=prompt_hash,
        llm_run=llm_run,
        error_class=error_class,
        error=str(error),
    )


def _looks_like_session(value: Any) -> bool:
    return hasattr(value, "add") and hasattr(value, "flush") and hasattr(value, "get")


def extract_strategy_candidate(
    document_version: Any,
    *positional: Any,
    session: Any = None,
    llm_client: Any = None,
    evidence: Any = None,
    classification: Mapping[str, Any] | None = None,
    document_text: str | None = None,
    model_name: str = "fixture",
    prompt_version: str = EXTRACTION_PROMPT_VERSION,
    estimated_cost_usd: float = 0.0,
    schema_path: str | None = None,
    quote_max_chars: int | None = None,
) -> ExtractionResult:
    """Run extraction from an ``extracted`` version into a validated candidate.

    For adapter compatibility, ``extract_strategy_candidate(session, version,
    client)`` is accepted in addition to keyword arguments.
    """

    if _looks_like_session(document_version):
        positional_values = list(positional)
        session, document_version = document_version, positional_values.pop(0)
        if positional_values and llm_client is None:
            llm_client = positional_values.pop(0)
        positional = tuple(positional_values)
    if positional:
        raise TypeError("unexpected positional extraction arguments")

    current = _state(document_version)
    if current in {"irrelevant", "background_only", "failed"}:
        return ExtractionResult(
            candidate=None,
            processing_status=current,
            document_version=document_version,
            error_class=(
                "not_relevant"
                if current != "failed"
                else _metadata(document_version).get("error_class")
            ),
        )
    if current == "classified":
        _set_state(document_version, "extracted")
    elif current != "extracted":
        raise state_machine.InvalidTransition(
            f"extraction requires classified or extracted state, got {current!r}"
        )

    body_text = _document_text(document_version, document_text)
    prompt = build_extraction_prompt(
        document_version,
        document_text=body_text,
        classification=_classification(document_version, classification),
    )
    try:
        client = llm_client or FixtureLLMClient()
        call: LLMCallResult = audited_complete(
            client,
            prompt,
            session=session,
            document_version=document_version,
            model_name=model_name,
            prompt_version=prompt_version,
            run_type="extraction",
            estimated_cost_usd=estimated_cost_usd,
        )
    except Exception as exc:
        result = _failure(
            document_version,
            error_class=getattr(exc, "error_class", "llm_call_failed"),
            error=exc,
            llm_run=getattr(exc, "llm_run", None),
            prompt=prompt,
            prompt_hash=None,
        )
        raise

    raw_response = call.response
    try:
        validated = validate_strategy_candidate(raw_response, schema_path=schema_path)
        explicit_evidence = evidence if evidence is not None else _payload_evidence(validated)
        evidence_entries = _accepted_evidence_entries(
            _normalise_evidence_input(explicit_evidence),
            document_version=document_version,
            normalized_text=body_text,
            quote_max_chars=quote_max_chars,
        )
        candidate, evidence_rows = candidate_from_validated(
            validated,
            document_version=document_version,
            evidence=evidence_entries,
            document_text=body_text,
            quote_max_chars=quote_max_chars,
        )
        # Re-run the guard on the safe, evidence-aware representation before
        # any ORM row is flushed.  This catches accidental mapper drift.
        safe_payload = _safe_candidate_payload(
            validated,
            evidence_entries,
        )
        validate_strategy_candidate(safe_payload, schema_path=schema_path)
        candidate, evidence_rows = _persist_candidate(
            session,
            candidate,
            evidence_rows,
        )
    except SchemaValidationError as exc:
        return _failure(
            document_version,
            error_class=exc.error_class,
            error=exc,
            llm_run=call.run,
            prompt=prompt,
            prompt_hash=call.prompt_hash,
            raw_response=raw_response,
        )
    except Exception as exc:
        return _failure(
            document_version,
            error_class=getattr(exc, "error_class", "candidate_mapping_failed"),
            error=exc,
            llm_run=call.run,
            prompt=prompt,
            prompt_hash=call.prompt_hash,
            raw_response=raw_response,
        )

    _set_state(document_version, "validated")
    metadata = _metadata(document_version)
    metadata["extraction"] = {
        "status": "validated",
        "candidate_id": candidate.candidate_id,
        "prompt_hash": call.prompt_hash,
        "llm_run_id": getattr(call.run, "llm_run_id", None),
        "evidence_count": len(evidence_rows),
    }
    _store_metadata(document_version, metadata)
    return ExtractionResult(
        candidate=candidate,
        processing_status=_state(document_version),
        document_version=document_version,
        raw_response=raw_response,
        validated_payload=safe_payload,
        prompt=prompt,
        prompt_hash=call.prompt_hash,
        llm_run=call.run,
        evidence=evidence_rows,
    )


def extract_candidate(*args: Any, **kwargs: Any) -> ExtractionResult:
    return extract_strategy_candidate(*args, **kwargs)


def extract_document(*args: Any, **kwargs: Any) -> ExtractionResult:
    return extract_strategy_candidate(*args, **kwargs)


__all__ = [
    "CORE_FIELDS",
    "CandidateMappingError",
    "EvidenceContractError",
    "ExtractionResult",
    "SchemaValidationError",
    "candidate_from_validated",
    "extract_candidate",
    "extract_document",
    "extract_strategy_candidate",
    "map_candidate",
]
