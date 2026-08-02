from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing import create_review_app
from scalping_briefing.config import load_config
from scalping_briefing.llm.audit import audited_complete
from scalping_briefing.llm.schema_guard import (
    SchemaValidationError,
    validate_strategy_candidate,
)
from scalping_briefing.models import Base, LLMRun
from scalping_briefing.pipeline.extract import CORE_FIELDS, map_candidate
from scalping_briefing.pipeline.routing import route_candidate
from scalping_briefing.pipeline.scoring import VALUE_SCORE_WEIGHTS, score_candidate


def _candidate_payload(version_id: str) -> dict[str, object]:
    return {
        "candidate_id": "protected-candidate",
        "canonical_name": "Queue Momentum",
        "summary": "A short-horizon queue observation.",
        "core_hypothesis": "Queue imbalance can precede movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter after queue imbalance.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit on reversal.",
        "exit_logic_status": "explicit",
        "required_data": ["L2 quotes"],
        "required_data_status": "explicit",
        "risk_notes": "Latency requires review.",
        "risk_notes_status": "explicit",
        "field_status": {
            field: "explicit"
            for field in CORE_FIELDS
        },
        "relevance_status": "relevant",
        "review_status": "pending",
        "source_confidence": 0.95,
        "extraction_confidence": 0.9,
        "document_version_ids": [version_id],
        "metadata": {},
    }


def test_p11_schema_invalid_llm_output_is_rejected_before_storage() -> None:
    with pytest.raises(SchemaValidationError):
        validate_strategy_candidate(
            {
                "candidate_id": "invalid-candidate",
                "canonical_name": "Invalid candidate",
                "unexpected_property": "must be rejected",
            }
        )


def test_p12_unverified_llm_output_is_not_stored_unchanged() -> None:
    version = {
        "document_version_id": "version-p12",
        "normalized_text": "The source contains no candidate evidence.",
    }
    raw_payload = _candidate_payload(version["document_version_id"])

    candidate, evidence_rows = map_candidate(
        raw_payload,
        document_version=version,
    )

    assert evidence_rows == []
    for field in CORE_FIELDS:
        assert getattr(candidate, field) is None
        assert getattr(candidate, f"{field}_status") == "unknown"
        assert candidate.field_status[field] == "unknown"
    assert candidate.review_status == "needs_review"
    assert raw_payload["entry_logic"] == "Enter after queue imbalance."


class _AuditedClient:
    def complete(self, _prompt: str) -> dict[str, str]:
        return {"result": "fixture response"}

    def metadata(self, _prompt: str) -> dict[str, object]:
        return {
            "model_name": "protected-test-model",
            "prompt_version": "protected-test-v1",
            "input_tokens": 7,
            "output_tokens": 5,
            "total_tokens": 12,
            "estimated_cost_usd": 0.125,
        }


def test_p13_llm_call_is_recorded_in_audit_log() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            call = audited_complete(
                _AuditedClient(),
                "audit this protected prompt",
                session=session,
                input_document_version_id="version-p13",
                model_name="fallback-model",
                prompt_version="fallback-v1",
                estimated_cost_usd=0.5,
            )

            run = session.scalar(select(LLMRun))
            assert run is call.run
            assert run is not None
            assert run.status == "success"
            assert run.model_name == "protected-test-model"
            assert run.prompt_version == "protected-test-v1"
            assert run.started_at is not None
            assert run.completed_at is not None
            assert run.input_document_version_id == "version-p13"
            assert run.input_tokens == 7
            assert run.output_tokens == 5
            assert run.total_tokens == 12
            assert run.estimated_cost_usd == pytest.approx(0.125)
            assert run.input_hash and run.input_hash.startswith("sha256:")
            assert run.output_hash and run.output_hash.startswith("sha256:")
            assert run.metadata_json["prompt_hash"] == call.prompt_hash
    finally:
        engine.dispose()


def test_p14_unsupported_value_score_is_rejected_and_breakdown_is_saved() -> None:
    unsupported = _candidate_payload("version-p14")
    unsupported["value_score"] = 101
    with pytest.raises(SchemaValidationError):
        validate_strategy_candidate(unsupported)

    candidate = _candidate_payload("version-p14")
    result = score_candidate(
        candidate,
        document_version={
            "document_version_id": "version-p14",
            "canonical_url": "https://example.invalid/queue",
            "published_at": "2026-08-01T12:00:00+00:00",
        },
        as_of=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
    )

    assert set(result.value_score_breakdown) == set(VALUE_SCORE_WEIGHTS)
    assert 0 <= result.value_score <= 100
    assert candidate["value_score"] == result.value_score
    assert all(
        detail["reason"].strip()
        for detail in result.value_score_breakdown.values()
    )


def test_p15_borderline_low_confidence_and_conflicting_cases_need_review() -> None:
    cases = (
        ("borderline_score", {"value_score": 50, "extraction_confidence": 0.9}),
        ("low_extraction_confidence", {"value_score": 100, "extraction_confidence": 0.69}),
        (
            "conflicting_core_field",
            {
                "value_score": 100,
                "extraction_confidence": 0.9,
                "required_data_status": "conflicting",
            },
        ),
    )
    settings = {
        "candidate_score_threshold": 60,
        "extraction_confidence_min": 0.7,
    }

    for expected_reason, overrides in cases:
        candidate = {
            "candidate_id": f"candidate-{expected_reason}",
            "value_score": overrides["value_score"],
            "extraction_confidence": overrides["extraction_confidence"],
            "entry_logic_status": "explicit",
            "exit_logic_status": "explicit",
            "required_data_status": overrides.get(
                "required_data_status", "explicit"
            ),
            "review_status": "pending",
        }
        document_version = {
            "document_version_id": f"version-{expected_reason}",
            "processing_status": "validated",
        }

        result = route_candidate(
            candidate,
            document_version=document_version,
            settings=settings,
        )

        assert result.processing_status == "needs_review"
        assert expected_reason in result.reasons
        assert candidate["review_status"] == "needs_review"
        assert document_version["processing_status"] == "needs_review"


def test_p16_review_app_fails_before_startup_without_token() -> None:
    for environment in ({}, {"REVIEW_API_TOKEN": ""}):
        settings = load_config(environ=environment)
        with pytest.raises(RuntimeError, match="REVIEW_API_TOKEN"):
            create_review_app(settings)
