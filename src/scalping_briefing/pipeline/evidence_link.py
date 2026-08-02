"""Create bounded, source-traceable Evidence rows for extracted fields.

This module is deliberately a pure linking boundary.  It does not collect
documents, call an LLM, write files, or persist ORM rows.  Extraction supplies
the quote provenance; this boundary checks that provenance against the exact
document version before constructing :class:`Evidence` objects.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from scalping_briefing.config import load_config
from scalping_briefing.models import Evidence
from scalping_briefing.models.base import new_id, utc_now
from scalping_briefing.publishing.gate import MAX_EVIDENCE_QUOTES


CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)
MAX_QUOTES_PER_CORE_FIELD = 2


class EvidenceLinkError(ValueError):
    """Raised when an extracted quote cannot be linked safely."""

    error_class = "evidence_link_failed"


class EvidenceContractError(EvidenceLinkError):
    """Compatibility name for callers using the extraction contract error."""

    error_class = "evidence_contract_failed"


class EvidenceProvenanceError(EvidenceContractError):
    """Raised when extraction has not accepted the requested quote."""


class EvidenceLimitError(EvidenceContractError):
    """Raised when a count or quote-length limit is exceeded."""


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any, name: str) -> str | None:
    candidate = _field(value, name)
    return candidate if isinstance(candidate, str) else None


def _version_id(document_version: Any) -> str:
    value = _field(document_version, "document_version_id")
    if value is None or not str(value).strip():
        raise EvidenceContractError("document_version_id is required")
    return str(value).strip()


def _candidate_id(value: Any) -> str:
    if value is None or not str(value).strip():
        raise EvidenceContractError("strategy_candidate_id is required")
    return str(value).strip()


def _document_text(document_version: Any, explicit: str | None) -> str:
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit.strip():
            raise EvidenceContractError("source document text is required")
        return explicit

    for name in ("normalized_text", "normalized_body", "text", "body", "content"):
        value = _text(document_version, name)
        if value is not None:
            return value

    location = _field(document_version, "normalized_location")
    if location:
        try:
            return Path(location).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise EvidenceContractError("source document text is unavailable") from exc
    raise EvidenceContractError("source document text is required")


def _compact(value: str) -> str:
    return " ".join(value.split())


def _quote_in_source(quote: str, source_text: str) -> bool:
    return quote in source_text or _compact(quote) in _compact(source_text)


def _entries(value: Any, *, default_field: str | None = None) -> list[dict[str, Any]]:
    """Normalise list and field-to-quote provenance forms without guessing fields."""

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
            if field_name in {
                "accepted_quotes",
                "accepted_evidence",
                "evidence",
                "quotes",
                "records",
                "items",
            }:
                result.extend(_entries(nested, default_field=default_field))
                continue
            result.extend(_entries(nested, default_field=str(field_name)))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for nested in value:
            if isinstance(nested, Mapping):
                item = dict(nested)
                if default_field is not None:
                    item.setdefault("field_name", default_field)
                result.append(item)
            elif isinstance(nested, str) and default_field is not None:
                result.append({"field_name": default_field, "quote": nested})
        return result
    if isinstance(value, str) and default_field is not None:
        return [{"field_name": default_field, "quote": value}]
    raise EvidenceContractError("evidence entries must be mappings or sequences")


def _provenance_entries(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        for key in (
            "accepted_quotes",
            "accepted_evidence",
            "evidence",
            "quotes",
            "records",
            "items",
        ):
            if key in value:
                return _entries(value[key])
    return _entries(value)


def _is_accepted(provenance: Mapping[str, Any]) -> bool:
    """Treat an explicit negative provenance marker as non-linkable.

    A value passed under ``accepted_quotes`` is already in the accepted set;
    older extraction callers may therefore omit a boolean marker.  Explicit
    false markers always win.
    """

    for key in (
        "accepted",
        "source_verified",
        "quote_verified",
        "verified",
        "accepted_by_extraction",
        "linkable",
    ):
        if key in provenance:
            return provenance[key] is True
    status = provenance.get("status")
    if status is not None:
        return str(status).strip().lower() in {"accepted", "verified", "valid"}
    return True


def _normalise_field(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError("evidence field_name is required")
    field_name = value.strip()
    if field_name not in CORE_FIELDS:
        raise EvidenceContractError(
            f"unsupported core evidence field {field_name!r}"
        )
    return field_name


def _normalise_quote(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceContractError("evidence quote is required")
    return value.strip()


def _quote_limit(value: int | None) -> int:
    selected = load_config().quote_max_chars if value is None else value
    try:
        limit = int(selected)
    except (TypeError, ValueError) as exc:
        raise EvidenceContractError("quote_max_chars must be an integer") from exc
    if limit < 1:
        raise EvidenceContractError("quote_max_chars must be positive")
    return limit


def _parse_captured_at(value: Any) -> datetime:
    if value is None:
        return utc_now()
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError as exc:
            raise EvidenceContractError("captured_at must be an ISO datetime") from exc
    raise EvidenceContractError("captured_at must be a datetime")


def _document_url(document_version: Any) -> str | None:
    value = _field(document_version, "canonical_url")
    if value is None:
        document = _field(document_version, "document")
        value = _field(document, "canonical_url") if document is not None else None
    return str(value) if value else None


def _metadata(item: Mapping[str, Any]) -> dict[str, Any]:
    reserved = {
        "accepted",
        "accepted_by_extraction",
        "captured_at",
        "document_version_id",
        "evidence_id",
        "field_name",
        "linkable",
        "quote",
        "quote_verified",
        "section_or_locator",
        "source_verified",
        "status",
        "strategy_candidate_id",
        "verified",
    }
    return {key: value for key, value in item.items() if key not in reserved}


def _matching_provenance(
    requested: Mapping[str, Any],
    provenance: Sequence[Mapping[str, Any]],
    *,
    version_id: str,
) -> Mapping[str, Any]:
    field_name = _normalise_field(requested.get("field_name"))
    quote = _normalise_quote(requested.get("quote"))
    for item in provenance:
        if not _is_accepted(item):
            continue
        try:
            provenance_field = _normalise_field(item.get("field_name"))
        except EvidenceContractError:
            continue
        provenance_quote = item.get("quote")
        if not isinstance(provenance_quote, str) or provenance_quote.strip() != quote:
            continue
        if provenance_field != field_name:
            continue
        provenance_version = item.get("document_version_id")
        if provenance_version is not None and str(provenance_version) != version_id:
            continue
        return item
    raise EvidenceProvenanceError(
        f"quote for {field_name!r} was not accepted by extraction provenance"
    )


def _existing_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping) and "evidence" in value:
        value = value["evidence"]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return [value]


def _validate_item_limit(
    existing_evidence: Any,
    new_count: int,
    max_evidence_quotes: int | None,
) -> None:
    if max_evidence_quotes is None:
        if existing_evidence is None:
            return
        max_evidence_quotes = MAX_EVIDENCE_QUOTES
    try:
        limit = int(max_evidence_quotes)
    except (TypeError, ValueError) as exc:
        raise EvidenceLimitError("MAX_EVIDENCE_QUOTES must be an integer") from exc
    if limit < 1 or limit > MAX_EVIDENCE_QUOTES:
        raise EvidenceLimitError(
            f"MAX_EVIDENCE_QUOTES must be between 1 and {MAX_EVIDENCE_QUOTES}"
        )
    existing_count = len(_existing_records(existing_evidence))
    if existing_count + new_count > limit:
        raise EvidenceLimitError(
            "item evidence count exceeds MAX_EVIDENCE_QUOTES "
            f"({limit})"
        )


def link_evidence(
    document_version: Any,
    strategy_candidate_id: str,
    quotes: Any = None,
    *,
    extraction_provenance: Any = None,
    source_text: str | None = None,
    quote_max_chars: int | None = None,
    max_evidence_quotes: int | None = None,
    existing_evidence: Any = None,
    evidence: Any = None,
    accepted_quotes: Any = None,
) -> list[Evidence]:
    """Link extraction-approved quotes to one candidate and document version.

    ``quotes`` contains the requested field/quote records.  The same records,
    or equivalent records, must be present in ``extraction_provenance`` (or
    ``accepted_quotes``).  Both the provenance match and source containment are
    required.  Validation happens before any ``Evidence`` object is created.

    ``max_evidence_quotes`` is optional because a candidate can accumulate
    Evidence for multiple core fields.  Supplying it, or supplying
    ``existing_evidence`` for a briefing-item context, applies the existing
    item-level :data:`MAX_EVIDENCE_QUOTES` contract.
    """

    if quotes is not None and evidence is not None:
        raise TypeError("use quotes or evidence, not both")
    requested_entries = _entries(quotes if quotes is not None else evidence)
    provenance_value = (
        extraction_provenance
        if extraction_provenance is not None
        else accepted_quotes
    )
    provenance_entries = _provenance_entries(provenance_value)
    if not provenance_entries:
        raise EvidenceProvenanceError("extraction provenance is required")
    if not requested_entries:
        return []

    version_id = _version_id(document_version)
    candidate_id = _candidate_id(strategy_candidate_id)
    document_body = _document_text(document_version, source_text)
    quote_limit = _quote_limit(quote_max_chars)

    existing = _existing_records(existing_evidence)
    field_counts = Counter(
        _field(item, "field_name")
        for item in existing
        if isinstance(_field(item, "field_name"), str)
    )
    _validate_item_limit(existing_evidence, len(requested_entries), max_evidence_quotes)

    prepared: list[tuple[dict[str, Any], Mapping[str, Any], str]] = []
    for requested in requested_entries:
        field_name = _normalise_field(requested.get("field_name"))
        quote = _normalise_quote(requested.get("quote"))
        if len(quote) > quote_limit:
            raise EvidenceLimitError(
                f"evidence quote exceeds quote_max_chars ({quote_limit})"
            )
        field_counts[field_name] += 1
        if field_counts[field_name] > MAX_QUOTES_PER_CORE_FIELD:
            raise EvidenceLimitError(
                f"evidence count exceeds two quotes for field {field_name!r}"
            )
        if not _quote_in_source(quote, document_body):
            raise EvidenceProvenanceError(
                f"quote for {field_name!r} is not present in source document"
            )

        provenance = _matching_provenance(
            requested,
            provenance_entries,
            version_id=version_id,
        )
        item = dict(provenance)
        item.update(requested)
        item["field_name"] = field_name
        item["quote"] = quote
        requested_locator = requested.get("section_or_locator")
        if not isinstance(requested_locator, str) or not requested_locator.strip():
            item["section_or_locator"] = provenance.get("section_or_locator")
        locator = item.get("section_or_locator")
        if not isinstance(locator, str) or not locator.strip():
            raise EvidenceContractError("evidence section_or_locator is required")
        item["section_or_locator"] = locator.strip()

        item_version = item.get("document_version_id")
        if item_version is not None and str(item_version) != version_id:
            raise EvidenceContractError(
                "evidence must point to the input document_version_id"
            )
        item_candidate = item.get("strategy_candidate_id")
        if item_candidate is not None and str(item_candidate) != candidate_id:
            raise EvidenceContractError(
                "evidence must point to the input strategy_candidate_id"
            )
        prepared.append((item, provenance, field_name))

    rows: list[Evidence] = []
    for item, _provenance, field_name in prepared:
        rows.append(
            Evidence(
                evidence_id=str(item.get("evidence_id") or new_id()),
                document_version_id=version_id,
                strategy_candidate_id=candidate_id,
                field_name=field_name,
                quote=item["quote"],
                section_or_locator=item["section_or_locator"],
                captured_at=_parse_captured_at(item.get("captured_at")),
                source_url=str(item.get("source_url") or _document_url(document_version) or "")
                or None,
                metadata=_metadata(item),
            )
        )
    return rows


def create_evidence_records(*args: Any, **kwargs: Any) -> list[Evidence]:
    """Descriptive alias for :func:`link_evidence`."""

    return link_evidence(*args, **kwargs)


def link_candidate_evidence(*args: Any, **kwargs: Any) -> list[Evidence]:
    """Compatibility alias for candidate-oriented callers."""

    return link_evidence(*args, **kwargs)


__all__ = [
    "CORE_FIELDS",
    "EvidenceContractError",
    "EvidenceLimitError",
    "EvidenceLinkError",
    "EvidenceProvenanceError",
    "MAX_EVIDENCE_QUOTES",
    "MAX_QUOTES_PER_CORE_FIELD",
    "create_evidence_records",
    "link_candidate_evidence",
    "link_evidence",
]
