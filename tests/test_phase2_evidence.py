from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalping_briefing.models import Evidence
from scalping_briefing.pipeline.evidence_link import (
    MAX_EVIDENCE_QUOTES,
    EvidenceLinkError,
    link_evidence,
)
from scalping_briefing.pipeline.validate import CORE_FIELDS, validate_extracted_candidate


SOURCE_TEXT = (
    "Queue imbalance can precede movement. "
    "Enter after queue imbalance. "
    "Exit on reversal."
)

CORE_FIELD_QUOTES = {
    "core_hypothesis": "Queue imbalance can precede movement.",
    "signal_inputs": "The signal uses queue imbalance and trade flow.",
    "entry_logic": "Enter after queue imbalance.",
    "exit_logic": "Exit on reversal.",
    "required_data": "Required data includes level-two quotes.",
    "risk_notes": "Latency creates adverse-selection risk.",
}
CORE_SOURCE_TEXT = " ".join(CORE_FIELD_QUOTES.values())


def _version(*, text: str = SOURCE_TEXT, version_id: str = "dv-1") -> dict[str, object]:
    return {
        "document_version_id": version_id,
        "processing_status": "extracted",
        "normalized_text": text,
        "canonical_url": "https://example.invalid/document",
        "metadata": {},
    }


def _quote(field_name: str, quote: str, *, version_id: str = "dv-1") -> dict[str, object]:
    return {
        "field_name": field_name,
        "quote": quote,
        "section_or_locator": "Fixture section",
        "document_version_id": version_id,
    }


def _provenance(*entries: dict[str, object]) -> dict[str, object]:
    return {"accepted_quotes": [{**entry, "accepted": True} for entry in entries]}


def _candidate(*, version_id: str = "dv-1") -> dict[str, object]:
    return {
        "candidate_id": "candidate-1",
        "canonical_name": "Queue Momentum",
        "summary": "A short-horizon queue observation.",
        "core_hypothesis": CORE_FIELD_QUOTES["core_hypothesis"],
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance", "trade flow"],
        "signal_inputs_status": "explicit",
        "entry_logic": CORE_FIELD_QUOTES["entry_logic"],
        "entry_logic_status": "explicit",
        "exit_logic": CORE_FIELD_QUOTES["exit_logic"],
        "exit_logic_status": "explicit",
        "required_data": ["level-two quotes"],
        "required_data_status": "explicit",
        "risk_notes": CORE_FIELD_QUOTES["risk_notes"],
        "risk_notes_status": "explicit",
        "field_status": {field: "explicit" for field in CORE_FIELDS},
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.9,
        "extraction_confidence": 0.8,
        "document_version_ids": [version_id],
        "metadata": {},
    }


@pytest.mark.parametrize(
    ("field_name", "quote"),
    CORE_FIELD_QUOTES.items(),
    ids=CORE_FIELD_QUOTES.keys(),
)
def test_each_core_field_has_an_independently_linked_evidence(
    field_name: str,
    quote: str,
) -> None:
    requested = _quote(field_name, quote)

    rows = link_evidence(
        document_version=_version(text=CORE_SOURCE_TEXT),
        strategy_candidate_id="candidate-1",
        quotes=[requested],
        extraction_provenance=_provenance(requested),
    )

    assert len(rows) >= 1
    row = next(row for row in rows if row.field_name == field_name)
    assert row.evidence_id
    assert row.document_version_id == "dv-1"
    assert row.strategy_candidate_id == "candidate-1"
    assert row.quote == quote
    assert row.section_or_locator == "Fixture section"
    assert isinstance(row.captured_at, datetime)
    assert row.captured_at.tzinfo is not None


def test_link_evidence_creates_traceable_seven_field_records() -> None:
    quote = _quote("core_hypothesis", "Queue imbalance can precede movement.")

    rows = link_evidence(
        document_version=_version(),
        strategy_candidate_id="candidate-1",
        quotes=[quote],
        extraction_provenance=_provenance(quote),
    )

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, Evidence)
    assert row.evidence_id
    assert row.document_version_id == "dv-1"
    assert row.strategy_candidate_id == "candidate-1"
    assert row.field_name == "core_hypothesis"
    assert row.quote == "Queue imbalance can precede movement."
    assert row.section_or_locator == "Fixture section"
    assert isinstance(row.captured_at, datetime)
    assert row.captured_at.tzinfo is not None
    assert row.captured_at.utcoffset() == UTC.utcoffset(row.captured_at)


def test_fabricated_or_unaccepted_quotes_are_rejected() -> None:
    fabricated = _quote("entry_logic", "This rule was invented by the extractor.")
    accepted_but_absent = _quote("entry_logic", "A quote absent from source.")

    with pytest.raises(EvidenceLinkError, match="source"):
        link_evidence(
            document_version=_version(),
            strategy_candidate_id="candidate-1",
            quotes=[fabricated],
            extraction_provenance=_provenance(fabricated),
        )

    with pytest.raises(EvidenceLinkError, match="provenance"):
        link_evidence(
            document_version=_version(),
            strategy_candidate_id="candidate-1",
            quotes=[accepted_but_absent],
            extraction_provenance={"accepted_quotes": []},
        )


def test_quote_limits_reject_field_and_item_overflow_and_configured_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = [
        _quote("entry_logic", "Enter after queue imbalance."),
        _quote("entry_logic", "Enter after queue imbalance."),
        _quote("entry_logic", "Enter after queue imbalance."),
    ]
    with pytest.raises(EvidenceLinkError, match="two quotes"):
        link_evidence(
            document_version=_version(),
            strategy_candidate_id="candidate-1",
            quotes=repeated,
            extraction_provenance=_provenance(*repeated),
        )

    item_quotes = [
        _quote("core_hypothesis", "Queue imbalance can precede movement."),
        _quote("entry_logic", "Enter after queue imbalance."),
        _quote("exit_logic", "Exit on reversal."),
    ]
    with pytest.raises(EvidenceLinkError, match="MAX_EVIDENCE_QUOTES"):
        link_evidence(
            document_version=_version(),
            strategy_candidate_id="candidate-1",
            quotes=item_quotes,
            extraction_provenance=_provenance(*item_quotes),
            max_evidence_quotes=MAX_EVIDENCE_QUOTES,
        )

    monkeypatch.setenv("quote_max_chars", "10")
    long_quote = _quote("entry_logic", "Enter after queue imbalance.")
    with pytest.raises(EvidenceLinkError, match="quote_max_chars"):
        link_evidence(
            document_version=_version(),
            strategy_candidate_id="candidate-1",
            quotes=[long_quote],
            extraction_provenance=_provenance(long_quote),
        )


def test_candidate_without_any_evidence_never_reaches_validated() -> None:
    version = _version(text=CORE_SOURCE_TEXT)

    result = validate_extracted_candidate(version, _candidate(), [])

    assert result.valid is False
    assert result.processing_status == "failed"
    assert version["processing_status"] == "failed"
    assert result.error_class == "evidence_contract_failed"
    assert version["metadata"]["validation"]["error_class"] == (  # type: ignore[index]
        "evidence_contract_failed"
    )


def test_candidate_with_partial_evidence_keeps_unevidenced_field_unpublishable() -> None:
    version = _version(text=CORE_SOURCE_TEXT)
    evidence = [_quote("core_hypothesis", CORE_FIELD_QUOTES["core_hypothesis"])]

    result = validate_extracted_candidate(version, _candidate(), evidence)

    assert result.valid is True
    assert result.processing_status == "validated"
    assert result.publishable_fields == ("core_hypothesis",)
    assert set(result.excluded_fields) == set(CORE_FIELDS) - {"core_hypothesis"}
    assert "entry_logic" not in result.publishable_fields
    assert "entry_logic" in result.excluded_fields
