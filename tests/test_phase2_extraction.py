from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.llm.fixture import FixtureLLMClient, FixtureMappingMissingError
from scalping_briefing.models import (
    Base,
    Document,
    DocumentVersion,
    Evidence,
    LLMRun,
    Source,
    StrategyCandidate,
)
from scalping_briefing.pipeline.classify import classify_document
from scalping_briefing.pipeline.extract import (
    EvidenceContractError,
    candidate_from_validated,
    extract_strategy_candidate,
)
from scalping_briefing.pipeline import state_machine


def _mapping_version(
    *,
    version_id: str = "dv-1",
    state: str = "extracted",
    text: str = "Queue imbalance is observed before a short-horizon entry.",
) -> dict:
    return {
        "document_version_id": version_id,
        "processing_status": state,
        "normalized_text": text,
        "canonical_url": "https://example.invalid/document",
        "metadata": {},
    }


def _candidate_payload(version_id: str = "dv-1") -> dict:
    return {
        "candidate_id": "candidate-1",
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
            "core_hypothesis": "explicit",
            "signal_inputs": "explicit",
            "entry_logic": "explicit",
            "exit_logic": "explicit",
            "required_data": "explicit",
            "risk_notes": "explicit",
        },
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.9,
        "extraction_confidence": 0.8,
        "document_version_ids": [version_id],
        "metadata": {},
    }


def _evidence(
    field_name: str,
    quote: str,
    *,
    version_id: str = "dv-1",
    evidence_id: str | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id or f"e-{field_name}",
        "document_version_id": version_id,
        "field_name": field_name,
        "quote": quote,
        "section_or_locator": "Fixture section",
    }


class RecordingClient:
    def __init__(self, response: object, metadata: dict | None = None) -> None:
        self.response = response
        self.recording_metadata = metadata or {}
        self.prompts: list[str] = []

    def complete(self, prompt: str, **_kwargs: object) -> object:
        self.prompts.append(prompt)
        return copy.deepcopy(self.response)

    def metadata(self, _prompt: str) -> dict:
        return dict(self.recording_metadata)


def _database_version(
    tmp_path: Path,
    *,
    version_id: str = "dv-1",
    state: str = "extracted",
    text: str = "Queue imbalance is observed before a short-horizon entry.",
) -> tuple[object, Session, DocumentVersion]:
    body_path = tmp_path / f"{version_id}.txt"
    body_path.write_text(text, encoding="utf-8")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = Source(
        source_id="fixture-source",
        name="Fixture source",
        type="fixture",
        base_url="fixture://fixture-source",
        connector_type="fixture",
    )
    document = Document(
        document_id=f"doc-{version_id}",
        source_id=source.source_id,
        canonical_url="https://example.invalid/document",
        original_url="https://example.invalid/document",
        title="Fixture document",
        collection_status="collected",
        processing_status=state,
        access_status="allowed",
        robots_allowed=True,
    )
    version = DocumentVersion(
        document_version_id=version_id,
        document_id=document.document_id,
        version_no=1,
        content_hash=f"sha256:{version_id}",
        body_hash=f"sha256:body-{version_id}",
        normalized_location=str(body_path),
        raw_location=str(body_path),
        collection_status="collected",
        processing_status=state,
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    session.add_all([source, document, version])
    session.flush()
    return engine, session, version


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Long-term investment valuation and dividend portfolio allocation.",
            "irrelevant",
        ),
        (
            "Market microstructure background: order book liquidity and price formation.",
            "background_only",
        ),
        (
            "A short-horizon scalping signal uses order book imbalance for entry and exit.",
            "relevant",
        ),
    ],
)
def test_classification_records_three_decisions_and_state_path(
    text: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = _mapping_version(state="collected", text=text)
    calls: list[tuple[str, str]] = []
    original_transition = state_machine.transition

    def observed_transition(current: str, target: str) -> str:
        calls.append((str(current), str(target)))
        return original_transition(current, target)

    monkeypatch.setattr(state_machine, "transition", observed_transition)
    result = classify_document(version)

    assert result.status == expected
    expected_state = expected if expected != "relevant" else "extracted"
    assert result.processing_status == expected_state
    assert version["processing_status"] == result.processing_status
    assert version["metadata"]["classification"]["reason"]["decision"] == expected
    assert calls[:3] == [
        ("collected", "normalized"),
        ("normalized", "deduplicated"),
        ("deduplicated", "classified"),
    ]
    final_target = "extracted" if expected == "relevant" else expected
    assert calls[-1] == ("classified", final_target)
    assert state_machine.can_transition("deduplicated", "classified")
    assert state_machine.can_transition("classified", final_target)


def test_schema_invalid_fixture_fails_extracted_without_candidate_record(
    tmp_path: Path,
) -> None:
    engine, session, version = _database_version(tmp_path)
    try:
        result = extract_strategy_candidate(
            version,
            session=session,
            llm_client=RecordingClient(
                {
                    "candidate_id": "invalid",
                    "canonical_name": "Invalid fixture",
                    "summary": "schema violation",
                    "unexpected_property": "reject",
                }
            ),
        )

        assert result.candidate is None
        assert result.processing_status == "failed"
        assert result.error_class
        assert version.processing_status == "failed"
        assert session.scalars(select(StrategyCandidate)).all() == []
        run = session.scalar(select(LLMRun))
        assert run is not None
        assert run.status == "success"
        assert run.input_document_version_id == version.document_version_id
    finally:
        session.close()
        engine.dispose()


def test_core_fields_without_evidence_are_unknown_and_audited(
    tmp_path: Path,
) -> None:
    engine, session, version = _database_version(tmp_path)
    try:
        response = _candidate_payload(version.document_version_id)
        result = extract_strategy_candidate(
            version,
            session=session,
            llm_client=RecordingClient(
                response,
                {
                    "model_name": "fixture-test-model",
                    "prompt_version": "phase2-extraction-test",
                    "input_tokens": 11,
                    "output_tokens": 13,
                    "total_tokens": 24,
                    "estimated_cost_usd": 0.125,
                },
            ),
        )

        assert result.processing_status == "validated"
        assert result.candidate is not None
        assert result.candidate.entry_logic is None
        assert result.candidate.entry_logic_status == "unknown"
        assert result.candidate.review_status == "needs_review"
        assert session.scalars(select(Evidence)).all() == []
        run = session.scalar(select(LLMRun))
        assert run is not None
        assert run.model_name == "fixture-test-model"
        assert run.prompt_version == "phase2-extraction-test"
        assert run.started_at is not None
        assert run.completed_at is not None
        assert run.input_document_version_id == version.document_version_id
        assert run.input_tokens == 11
        assert run.output_tokens == 13
        assert run.total_tokens == 24
        assert run.estimated_cost_usd == pytest.approx(0.125)
    finally:
        session.close()
        engine.dispose()


def test_missing_fixture_mapping_fails_immediately_and_records_error(
    tmp_path: Path,
) -> None:
    mapping_path = tmp_path / "empty-response-map.json"
    mapping_path.write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    engine, session, version = _database_version(tmp_path)
    client = FixtureLLMClient(mapping_path)
    try:
        with pytest.raises(FixtureMappingMissingError):
            extract_strategy_candidate(version, session=session, llm_client=client)

        assert client.calls == 0
        assert version.processing_status == "failed"
        assert version.metadata_json["error_class"]
        assert session.scalars(select(StrategyCandidate)).all() == []
        run = session.scalar(select(LLMRun))
        assert run is not None
        assert run.status == "failed"
        assert run.error
        assert run.input_document_version_id == version.document_version_id
    finally:
        session.close()
        engine.dispose()


def test_evidence_quote_must_be_in_normalized_body_and_downgrades_field() -> None:
    version = _mapping_version(
        text="Queue imbalance can precede movement."
    )
    payload = _candidate_payload()
    evidence = [
        _evidence("core_hypothesis", "Queue imbalance can precede movement."),
        _evidence("entry_logic", "This unsupported entry rule is not present."),
    ]

    candidate, evidence_rows = candidate_from_validated(
        payload,
        document_version=version,
        evidence=evidence,
    )

    assert candidate.core_hypothesis == "Queue imbalance can precede movement."
    assert candidate.core_hypothesis_status == "explicit"
    assert candidate.entry_logic is None
    assert candidate.entry_logic_status == "unknown"
    assert candidate.review_status == "needs_review"
    assert [row.field_name for row in evidence_rows] == ["core_hypothesis"]


def test_evidence_count_and_configured_quote_length_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = _mapping_version(text="one two three four five six seven eight nine ten eleven")
    payload = _candidate_payload()
    three_quotes = [
        _evidence("entry_logic", "one"),
        _evidence("entry_logic", "two"),
        _evidence("entry_logic", "three"),
    ]
    with pytest.raises(EvidenceContractError, match="two quotes"):
        candidate_from_validated(
            payload,
            document_version=version,
            evidence=three_quotes,
        )

    monkeypatch.setenv("quote_max_chars", "10")
    with pytest.raises(EvidenceContractError, match="quote_max_chars"):
        candidate_from_validated(
            payload,
            document_version=version,
            evidence=[_evidence("entry_logic", "one two three")],
        )
