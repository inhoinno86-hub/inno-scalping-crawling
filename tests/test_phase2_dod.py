from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scalping_briefing.llm.fixture import (
    DEFAULT_FIXTURE_MAPPING,
    FixtureLLMClient,
    prompt_hash,
)
from scalping_briefing.llm.prompts import (
    build_classification_prompt,
    build_extraction_prompt,
)
from scalping_briefing.models import (
    Base,
    Document,
    DocumentVersion,
    Evidence,
    Review,
    Source,
    StrategyCandidate,
)
from scalping_briefing.pipeline.classify import classify_document
from scalping_briefing.pipeline.routing import route_candidate
from scalping_briefing.pipeline.scoring import VALUE_SCORE_WEIGHTS, score_candidate
from scalping_briefing.pipeline.extract import extract_strategy_candidate
from scalping_briefing.pipeline.validate import CORE_FIELDS
from scalping_briefing.publishing.candidate_view import build_candidate_view
from scalping_briefing.publishing.gate import MissingEvidenceError
from scalping_briefing.review import ReviewService


SOURCE_TEXT = (
    "Queue imbalance can precede short-horizon movement. "
    "queue imbalance and order book are the signal inputs. "
    "Enter after the documented queue imbalance condition. "
    "Exit on reversal or the documented holding timeout. "
    "L2 quotes and trades are required data. "
    "Latency and adverse selection require review."
)


def _database() -> tuple[Engine, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def _collected_version(tmp_path: Path) -> tuple[Engine, Session, DocumentVersion]:
    body_path = tmp_path / "fixture-normalized.txt"
    body_path.write_text(SOURCE_TEXT, encoding="utf-8")
    engine, session = _database()
    source = Source(
        source_id="phase2-dod-fixture-source",
        name="Phase 2 DoD fixture source",
        type="fixture",
        base_url="fixture://phase2-dod",
        connector_type="fixture",
        active=True,
        metadata={},
    )
    document = Document(
        document_id="phase2-dod-document",
        source_id=source.source_id,
        canonical_url="https://example.invalid/research/queue-momentum",
        original_url="https://example.invalid/research/queue-momentum",
        title="Queue Momentum Update",
        collection_status="collected",
        processing_status="collected",
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    version = DocumentVersion(
        document_version_id="fixture-document-version-1",
        document_id=document.document_id,
        version_no=1,
        content_hash="sha256:phase2-dod-fixture",
        body_hash="sha256:phase2-dod-fixture-body",
        normalized_location=str(body_path),
        raw_location=str(body_path),
        collection_status="collected",
        processing_status="collected",
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    session.add_all([source, document, version])
    session.flush()
    return engine, session, version


def _recorded_fixture(case: str) -> dict[str, Any]:
    payload = json.loads(DEFAULT_FIXTURE_MAPPING.read_text(encoding="utf-8"))
    for record in payload["mappings"].values():
        if record.get("fixture_case") == case:
            return copy.deepcopy(record)
    raise AssertionError(f"missing recorded fixture case: {case}")


def _write_fixture_map(
    mapping_path: Path,
    records: list[tuple[str, dict[str, Any]]],
) -> None:
    mapping_path.write_text(
        json.dumps(
            {
                "recording_version": 1,
                "recorded_at": "2026-08-03T00:00:00Z",
                "mappings": {
                    prompt_hash(prompt): record for prompt, record in records
                },
            }
        ),
        encoding="utf-8",
    )


def _publication_payload(
    *, version_id: str = "dv-publication-1"
) -> dict[str, Any]:
    return {
        "candidate_id": "candidate-publication-1",
        "canonical_name": "Queue Momentum",
        "summary": "A bounded short-horizon queue observation.",
        "core_hypothesis": "Queue imbalance can precede short-horizon movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance", "order book"],
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


def _publication_evidence(
    field_name: str,
    quote: str,
    *,
    version_id: str = "dv-publication-1",
) -> dict[str, Any]:
    return {
        "evidence_id": f"e-{field_name}",
        "document_version_id": version_id,
        "strategy_candidate_id": "candidate-publication-1",
        "field_name": field_name,
        "quote": quote,
        "section_or_locator": f"{field_name} section",
        "captured_at": datetime(2026, 8, 3, tzinfo=UTC),
        "source_url": "https://example.invalid/research/queue-momentum",
    }


def _review_candidate(candidate_id: str, version_id: str) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=candidate_id,
        canonical_name="Queue Momentum",
        summary="A bounded review candidate.",
        core_hypothesis="Queue imbalance precedes short-horizon movement.",
        core_hypothesis_status="explicit",
        signal_inputs=["queue imbalance"],
        signal_inputs_status="explicit",
        entry_logic="Enter after the documented condition.",
        entry_logic_status="explicit",
        exit_logic="Exit on reversal.",
        exit_logic_status="explicit",
        required_data=["L2 quotes"],
        required_data_status="explicit",
        risk_notes="Latency requires review.",
        risk_notes_status="explicit",
        field_status={field: "explicit" for field in CORE_FIELDS},
        relevance_status="relevant",
        review_status="needs_review",
        source_confidence=0.9,
        extraction_confidence=0.9,
        document_version_ids=[version_id],
        metadata={},
    )


def _scoring_candidate() -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id="candidate-score-persisted",
        canonical_name="Queue Momentum",
        summary="A reproducible short-horizon queue strategy.",
        core_hypothesis="Queue imbalance can precede short-horizon movement.",
        core_hypothesis_status="explicit",
        signal_inputs=["queue imbalance", "order book"],
        signal_inputs_status="explicit",
        entry_logic="Enter when queue imbalance exceeds the documented threshold.",
        entry_logic_status="explicit",
        exit_logic="Exit on reversal or after the documented timeout.",
        exit_logic_status="explicit",
        required_data=["L2 quotes", "trades"],
        required_data_status="explicit",
        required_frequency="tick / sub-second",
        risk_notes="Latency and adverse selection require review.",
        risk_notes_status="explicit",
        asset_classes=["crypto"],
        market_types=["order book"],
        strategy_families=["momentum"],
        holding_horizon="seconds to minutes",
        microstructure_level="L2",
        field_status={field: "explicit" for field in CORE_FIELDS},
        relevance_status="relevant",
        review_status="pending",
        source_confidence=0.95,
        extraction_confidence=0.9,
        novelty_status="new",
        document_version_ids=["version-score-persisted"],
        metadata={
            "source_type": "official research",
            "author_or_org": "Example Exchange Research",
            "published_at": "2026-08-01T12:00:00+00:00",
            "license": "CC BY 4.0",
            "source_version_ref": "rev-42",
        },
    )


def _routing_candidate(
    candidate_id: str,
    *,
    value_score: int,
    extraction_confidence: float,
    conflicting_field: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate: dict[str, Any] = {
        "candidate_id": candidate_id,
        "value_score": value_score,
        "extraction_confidence": extraction_confidence,
        "entry_logic_status": "explicit",
        "exit_logic_status": "explicit",
        "required_data_status": "explicit",
        "review_status": "pending",
    }
    if conflicting_field is not None:
        candidate[f"{conflicting_field}_status"] = "conflicting"
    return candidate, {
        "document_version_id": f"{candidate_id}-version",
        "processing_status": "validated",
    }


def test_phase2_dod1_collected_document_becomes_candidate_with_evidence(
    tmp_path: Path,
) -> None:
    engine, session, version = _collected_version(tmp_path)
    mapping_path = tmp_path / "phase2-dod-response-map.json"
    try:
        classification_prompt = build_classification_prompt(version)
        classification_record = _recorded_fixture("normal_classification")
        _write_fixture_map(
            mapping_path,
            [(classification_prompt, classification_record)],
        )

        classification = classify_document(
            version,
            session=session,
            llm_client=FixtureLLMClient(mapping_path),
            use_llm=True,
        )

        assert classification.status == "relevant"
        assert classification.processing_status == "extracted"
        assert version.processing_status == "extracted"

        extraction_prompt = build_extraction_prompt(
            version,
            classification=version.metadata_json["classification"],
        )
        _write_fixture_map(
            mapping_path,
            [
                (classification_prompt, classification_record),
                (extraction_prompt, _recorded_fixture("normal")),
            ],
        )

        extraction = extract_strategy_candidate(
            version,
            session=session,
            llm_client=FixtureLLMClient(mapping_path),
        )

        assert extraction.error_class is None
        assert extraction.processing_status == "validated"
        assert extraction.validated_payload is not None
        assert extraction.candidate is not None
        assert extraction.candidate.candidate_id == "fixture-candidate-normal"
        assert extraction.candidate.document_version_ids == [
            version.document_version_id
        ]
        assert version.processing_status == "validated"
        assert len(extraction.evidence or []) == len(CORE_FIELDS)
        assert {
            row.field_name for row in extraction.evidence or []
        } == set(CORE_FIELDS)
        assert all(
            row.document_version_id == version.document_version_id
            and row.strategy_candidate_id == extraction.candidate.candidate_id
            for row in extraction.evidence or []
        )
        assert session.scalars(select(Evidence)).all() == extraction.evidence
        assert session.get(StrategyCandidate, extraction.candidate.candidate_id) is not None
    finally:
        session.close()
        engine.dispose()


def test_phase2_dod2_core_field_without_evidence_is_not_publishable() -> None:
    evidence = [
        _publication_evidence(
            "core_hypothesis",
            "Queue imbalance can precede short-horizon movement.",
        ),
        _publication_evidence(
            "signal_inputs",
            "Queue imbalance and order book are the signal inputs.",
        ),
        _publication_evidence(
            "exit_logic",
            "Exit on reversal or the documented holding timeout.",
        ),
        _publication_evidence(
            "required_data",
            "L2 quotes and trades are required data.",
        ),
        _publication_evidence(
            "risk_notes",
            "Latency and adverse selection require review.",
        ),
    ]

    with pytest.raises(MissingEvidenceError):
        build_candidate_view(_publication_payload(), evidence)


def test_phase2_dod3_review_decision_recorded_with_reviewer_and_source_version() -> None:
    engine, session = _database()
    source = Source(
        source_id="source-review-dod",
        name="Fixture review source",
        type="fixture",
        base_url="https://example.invalid",
        connector_type="fixture",
        active=True,
        metadata={},
    )
    document = Document(
        document_id="document-review-dod",
        source_id=source.source_id,
        canonical_url="https://example.invalid/research/queue-momentum",
        original_url="https://example.invalid/research/queue-momentum",
        title="Queue Momentum Review",
        collection_status="collected",
        processing_status="collected",
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    version = DocumentVersion(
        document_version_id="version-review-dod",
        document_id=document.document_id,
        content_hash="hash-review-dod",
        collection_status="collected",
        processing_status="collected",
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    candidate = _review_candidate("candidate-review-dod", version.document_version_id)
    evidence = [
        Evidence(
            evidence_id=f"evidence-review-{field}",
            document_version_id=version.document_version_id,
            strategy_candidate_id=candidate.candidate_id,
            field_name=field,
            quote=f"Evidence for {field}.",
            section_or_locator=f"{field} section",
            captured_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
        for field in CORE_FIELDS
    ]
    try:
        session.add_all([source, document, version, candidate, *evidence])
        session.commit()

        review = ReviewService(session).record_decision(
            candidate.candidate_id,
            "reviewer-phase2",
            "approved",
            "Reviewed against source Evidence.",
        )
        session.commit()
        session.expire_all()

        persisted_review = session.scalar(
            select(Review).where(Review.review_id == review.review_id)
        )
        assert persisted_review is not None
        assert persisted_review.reviewer_id == "reviewer-phase2"
        assert persisted_review.decision == "approved"
        assert isinstance(persisted_review.reviewed_at, datetime)

        view = ReviewService(session).get_candidate(candidate.candidate_id)
        assert view is not None
        assert view["document_version_id"] == version.document_version_id
        assert view["document_version"]["document_version_id"] == (
            version.document_version_id
        )
        assert all(
            item["document_version_id"] == version.document_version_id
            for item in view["evidence"]
        )
    finally:
        session.close()
        engine.dispose()


def test_phase2_dod4_value_score_breakdown_persisted_with_reasons() -> None:
    engine, session = _database()
    candidate = _scoring_candidate()
    document_version = {
        "document_version_id": "version-score-persisted",
        "canonical_url": "https://example.invalid/research/queue-momentum",
        "published_at": "2026-08-01T12:00:00+00:00",
        "updated_at": "2026-08-01T12:00:00+00:00",
        "source_version_ref": "rev-42",
    }
    as_of = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    try:
        session.add(candidate)
        session.commit()

        result = score_candidate(
            candidate,
            document_version=document_version,
            as_of=as_of,
        )
        session.commit()
        session.expire_all()

        persisted = session.get(StrategyCandidate, candidate.candidate_id)
        assert persisted is not None
        assert persisted.value_score == result.value_score
        assert persisted.value_score_breakdown == result.value_score_breakdown
        assert set(persisted.value_score_breakdown) == set(VALUE_SCORE_WEIGHTS)
        assert persisted.value_score == sum(
            detail["score"]
            for detail in persisted.value_score_breakdown.values()
        )
        for criterion, maximum in VALUE_SCORE_WEIGHTS.items():
            detail = persisted.value_score_breakdown[criterion]
            assert detail["max_score"] == maximum
            assert 0 <= detail["score"] <= maximum
            assert isinstance(detail["reason"], str)
            assert detail["reason"].strip()
    finally:
        session.close()
        engine.dispose()


def test_phase2_dod5_borderline_or_low_confidence_or_conflicting_goes_to_needs_review() -> None:
    borderline_candidate, borderline_version = _routing_candidate(
        "candidate-borderline",
        value_score=50,
        extraction_confidence=0.9,
    )
    borderline = route_candidate(
        borderline_candidate,
        document_version=borderline_version,
        candidate_score_threshold=60,
        extraction_confidence_min=0.7,
    )
    assert borderline.processing_status == "needs_review"
    assert borderline.reasons == ("borderline_score",)
    assert borderline_version["processing_status"] == "needs_review"
    assert borderline_candidate["review_status"] == "needs_review"

    low_confidence_candidate, low_confidence_version = _routing_candidate(
        "candidate-low-confidence",
        value_score=100,
        extraction_confidence=0.69,
    )
    low_confidence = route_candidate(
        low_confidence_candidate,
        document_version=low_confidence_version,
        candidate_score_threshold=60,
        extraction_confidence_min=0.7,
    )
    assert low_confidence.processing_status == "needs_review"
    assert low_confidence.reasons == ("low_extraction_confidence",)
    assert low_confidence_version["processing_status"] == "needs_review"
    assert low_confidence_candidate["review_status"] == "needs_review"

    conflicting_candidate, conflicting_version = _routing_candidate(
        "candidate-conflicting",
        value_score=100,
        extraction_confidence=0.9,
        conflicting_field="required_data",
    )
    conflicting = route_candidate(
        conflicting_candidate,
        document_version=conflicting_version,
        candidate_score_threshold=60,
        extraction_confidence_min=0.7,
    )
    assert conflicting.processing_status == "needs_review"
    assert conflicting.reasons == ("conflicting_core_field",)
    assert conflicting.conflicting_fields == ("required_data",)
    assert conflicting_version["processing_status"] == "needs_review"
    assert conflicting_candidate["review_status"] == "needs_review"
