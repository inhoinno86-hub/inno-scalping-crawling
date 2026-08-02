from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalping_briefing.pipeline import state_machine
from scalping_briefing.pipeline.validate import (
    CORE_FIELDS,
    validate_extracted_candidate,
)
from scalping_briefing.publishing import gate
from scalping_briefing.publishing.candidate_view import build_candidate_view
from scalping_briefing.publishing.gate import (
    EvidenceQuoteError,
    MissingEvidenceError,
    PublicationPhraseError,
)


SOURCE_TEXT = (
    "Queue imbalance can precede short-horizon movement. "
    "Queue imbalance and order book are the signal inputs. "
    "Enter after the documented queue imbalance condition. "
    "Exit on reversal or the documented holding timeout. "
    "L2 quotes and trades are required data. "
    "Latency and adverse selection require review."
)


def _version(*, state: str = "extracted", text: str = SOURCE_TEXT) -> dict[str, object]:
    return {
        "document_version_id": "dv-validation-1",
        "processing_status": state,
        "normalized_text": text,
        "canonical_url": "https://example.invalid/validation",
        "metadata": {},
    }


def _payload(*, version_id: str = "dv-validation-1") -> dict[str, object]:
    return {
        "candidate_id": "candidate-validation-1",
        "canonical_name": "Queue Momentum",
        "summary": "A short-horizon queue observation.",
        "core_hypothesis": "Queue imbalance can precede short-horizon movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["Queue imbalance", "order book"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter after the documented queue imbalance condition.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit on reversal or the documented holding timeout.",
        "exit_logic_status": "explicit",
        "required_data": ["L2 quotes", "trades"],
        "required_data_status": "explicit",
        "risk_notes": "Latency and adverse selection require review.",
        "risk_notes_status": "explicit",
        "field_status": {field: "explicit" for field in CORE_FIELDS},
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.9,
        "extraction_confidence": 0.8,
        "document_version_ids": [version_id],
        "metadata": {},
    }


def _evidence(field_name: str, quote: str, *, version_id: str = "dv-validation-1") -> dict[str, object]:
    return {
        "evidence_id": f"e-{field_name}",
        "document_version_id": version_id,
        "strategy_candidate_id": "candidate-validation-1",
        "field_name": field_name,
        "quote": quote,
        "section_or_locator": f"{field_name} section",
        "captured_at": datetime(2026, 8, 2, tzinfo=UTC),
        "source_url": "https://example.invalid/validation",
    }


def _all_evidence() -> list[dict[str, object]]:
    return [
        _evidence(
            "core_hypothesis",
            "Queue imbalance can precede short-horizon movement.",
        ),
        _evidence(
            "signal_inputs",
            "Queue imbalance and order book are the signal inputs.",
        ),
        _evidence(
            "entry_logic",
            "Enter after the documented queue imbalance condition.",
        ),
        _evidence(
            "exit_logic",
            "Exit on reversal or the documented holding timeout.",
        ),
        _evidence("required_data", "L2 quotes and trades are required data."),
        _evidence("risk_notes", "Latency and adverse selection require review."),
    ]


def test_valid_extracted_candidate_transitions_to_validated_with_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _version()
    calls: list[tuple[str, str]] = []
    original_transition = state_machine.transition

    def observed_transition(current: str, target: str) -> str:
        calls.append((str(current), str(target)))
        return original_transition(current, target)

    monkeypatch.setattr(state_machine, "transition", observed_transition)

    result = validate_extracted_candidate(
        version,
        _payload(),
        _all_evidence(),
    )

    assert result.valid is True
    assert result.processing_status == "validated"
    assert version["processing_status"] == "validated"
    assert result.error_class is None
    assert len(result.evidence) == len(CORE_FIELDS)
    assert result.publishable is True
    assert result.publishable_fields == CORE_FIELDS
    assert result.excluded_fields == ()
    assert calls == [("extracted", "validated")]
    assert version["metadata"]["validation"]["status"] == "validated"  # type: ignore[index]


def test_schema_failure_transitions_extracted_to_failed_without_candidate() -> None:
    version = _version()
    invalid = _payload()
    del invalid["summary"]

    result = validate_extracted_candidate(version, invalid, _all_evidence())

    assert result.valid is False
    assert result.candidate is None
    assert result.evidence == []
    assert result.processing_status == "failed"
    assert version["processing_status"] == "failed"
    assert result.error_class == "schema_validation_error"
    assert version["metadata"]["error_class"]  # type: ignore[index]
    assert version["metadata"]["validation"]["status"] == "failed"  # type: ignore[index]


def test_invalid_evidence_transitions_extracted_to_failed_without_publishable_result() -> None:
    version = _version()
    invalid_evidence = _all_evidence()
    invalid_evidence[0]["quote"] = "This quote is not in the normalized source."

    result = validate_extracted_candidate(version, _payload(), invalid_evidence)

    assert result.valid is False
    assert result.candidate is None
    assert result.publishable is False
    assert result.processing_status == "failed"
    assert result.error_class
    assert "evidence" in result.error_class
    assert version["metadata"]["error_class"] == result.error_class  # type: ignore[index]


def test_core_field_without_evidence_is_excluded_from_publishable_fields() -> None:
    version = _version()
    partial = [item for item in _all_evidence() if item["field_name"] != "entry_logic"]

    result = validate_extracted_candidate(version, _payload(), partial)

    assert result.valid is True
    assert result.processing_status == "validated"
    assert result.publishable is False
    assert "entry_logic" not in result.publishable_fields
    assert "entry_logic" in result.excluded_fields
    assert version["metadata"]["validation"]["excluded_fields"] == [  # type: ignore[index]
        "entry_logic"
    ]


def test_candidate_with_no_evidence_is_failed_and_not_publishable() -> None:
    version = _version()

    result = validate_extracted_candidate(version, _payload(), [])

    assert result.valid is False
    assert result.processing_status == "failed"
    assert version["processing_status"] == "failed"
    assert result.evidence == []
    assert result.publishable is False
    assert result.publishable_fields == ()
    assert result.excluded_fields == CORE_FIELDS
    assert result.error_class == "evidence_contract_failed"

    with pytest.raises(MissingEvidenceError):
        build_candidate_view(_payload(), [])


def test_evidence_in_candidate_metadata_is_validated_when_argument_is_omitted() -> None:
    version = _version()
    payload = _payload()
    payload["metadata"] = {"evidence": _all_evidence()}

    result = validate_extracted_candidate(payload, document_version=version)

    assert result.valid is True
    assert result.processing_status == "validated"
    assert len(result.evidence) == len(CORE_FIELDS)
    assert result.publishable is True


def test_candidate_view_converts_each_core_field_and_calls_publication_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    original_validate_publication = gate.validate_publication

    def observed_validate_publication(publication: object, **limits: int) -> object:
        calls.append(publication)
        return original_validate_publication(publication, **limits)

    monkeypatch.setattr(gate, "validate_publication", observed_validate_publication)

    view = build_candidate_view(_payload(), _all_evidence())

    assert len(calls) == 1
    assert calls[0] is view
    assert [item["field_name"] for item in view["items"]] == list(CORE_FIELDS)  # type: ignore[index]
    assert all(
        item["strategy_candidate_id"] == "candidate-validation-1"
        for item in view["items"]  # type: ignore[index]
    )
    assert all(len(item["evidence"]) == 1 for item in view["items"])  # type: ignore[index]


def test_candidate_view_rejects_a_core_field_without_evidence() -> None:
    evidence = [
        item for item in _all_evidence() if item["field_name"] != "entry_logic"
    ]

    with pytest.raises(MissingEvidenceError):
        build_candidate_view(_payload(), evidence)


def test_candidate_view_routes_banned_phrases_through_publication_gate() -> None:
    candidate = _payload()
    candidate["summary"] = "투자 추천"

    with pytest.raises(PublicationPhraseError):
        build_candidate_view(candidate, _all_evidence())


def test_candidate_view_routes_item_quote_limits_through_publication_gate() -> None:
    too_many = _all_evidence() + [
        _evidence("core_hypothesis", "Queue imbalance can precede short-horizon movement."),
        _evidence("core_hypothesis", "Queue imbalance and order book are the signal inputs."),
    ]

    with pytest.raises(EvidenceQuoteError):
        build_candidate_view(_payload(), too_many)

    too_long = _all_evidence()
    too_long[0]["quote"] = "x" * 301

    with pytest.raises(EvidenceQuoteError):
        build_candidate_view(_payload(), too_long)
