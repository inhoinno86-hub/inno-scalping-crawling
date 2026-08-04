from __future__ import annotations

from datetime import UTC, datetime

import pytest

from scalping_briefing.pipeline.routing import route_candidate
from scalping_briefing.pipeline.validate import CORE_FIELDS, validate_extracted_candidate
from scalping_briefing.publishing.gate import PublicationGateError, validate_publication


def _version() -> dict[str, object]:
    return {
        "document_version_id": "dv-phase3-conflicting",
        "processing_status": "extracted",
        "normalized_text": " ".join(
            f"Evidence for {field}." for field in CORE_FIELDS
        ),
        "canonical_url": "https://example.invalid/conflicting",
        "metadata": {},
    }


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "candidate-phase3-conflicting",
        "canonical_name": "Conflicting Queue Momentum",
        "summary": "A candidate whose core fields require human review.",
        "core_hypothesis": "Conflicting hypothesis.",
        "core_hypothesis_status": "conflicting",
        "signal_inputs": ["conflicting signal"],
        "signal_inputs_status": "conflicting",
        "entry_logic": "Conflicting entry rule.",
        "entry_logic_status": "conflicting",
        "exit_logic": "Conflicting exit rule.",
        "exit_logic_status": "conflicting",
        "required_data": ["conflicting data"],
        "required_data_status": "conflicting",
        "risk_notes": "Conflicting risk notes.",
        "risk_notes_status": "conflicting",
        "field_status": {field: "conflicting" for field in CORE_FIELDS},
        "relevance_status": "relevant",
        "review_status": "pending",
        "source_confidence": 0.9,
        "extraction_confidence": 0.9,
        "document_version_ids": ["dv-phase3-conflicting"],
        "metadata": {},
    }


def _evidence() -> list[dict[str, object]]:
    return [
        {
            "evidence_id": f"e-{field}",
            "document_version_id": "dv-phase3-conflicting",
            "strategy_candidate_id": "candidate-phase3-conflicting",
            "field_name": field,
            "quote": f"Evidence for {field}.",
            "section_or_locator": f"{field} section",
            "captured_at": datetime(2026, 8, 3, tzinfo=UTC),
        }
        for field in CORE_FIELDS
    ]


def test_all_conflicting_core_fields_validate_then_route_to_needs_review() -> None:
    version = _version()

    validated = validate_extracted_candidate(version, _candidate(), _evidence())

    assert validated.valid is True
    assert validated.processing_status == "validated"
    assert validated.publishable_fields == ()
    assert validated.excluded_fields == CORE_FIELDS
    assert version["metadata"]["validation"]["reason"] == "no_publishable_fields"  # type: ignore[index]

    routed = route_candidate(validated)

    assert routed.processing_status == "needs_review"
    assert routed.conflicting_fields == CORE_FIELDS
    assert version["processing_status"] == "needs_review"
    assert validated.candidate["review_status"] == "needs_review"  # type: ignore[index]

    with pytest.raises(PublicationGateError):
        validate_publication(validated.as_dict())
