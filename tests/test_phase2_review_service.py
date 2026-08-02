from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scalping_briefing.models import (
    Base,
    Document,
    DocumentVersion,
    Evidence,
    Review,
    Source,
    StrategyCandidate,
)
from scalping_briefing.pipeline import state_machine
from scalping_briefing.pipeline.validate import CORE_FIELDS
from scalping_briefing.review import ReviewService


def _candidate(candidate_id: str, status: str) -> StrategyCandidate:
    values: dict[str, object] = {
        "candidate_id": candidate_id,
        "canonical_name": f"Candidate {candidate_id}",
        "summary": "A bounded review candidate.",
        "core_hypothesis": "Queue imbalance precedes short-horizon movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter after the documented condition.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit on reversal.",
        "exit_logic_status": "explicit",
        "required_data": ["L2 quotes"],
        "required_data_status": "explicit",
        "risk_notes": "Latency requires review.",
        "risk_notes_status": "explicit",
        "field_status": {field: "explicit" for field in CORE_FIELDS},
        "relevance_status": "relevant",
        "review_status": status,
        "source_confidence": 0.9,
        "extraction_confidence": 0.9,
        "document_version_ids": ["version-1"],
        "metadata": {},
    }
    return StrategyCandidate(**values)


def _database() -> tuple[Engine, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_get_candidate_returns_source_link_document_version_and_evidence_quotes() -> None:
    engine, session = _database()
    try:
        source = Source(
            source_id="source-1",
            name="Fixture source",
            type="fixture",
            base_url="https://example.invalid",
            connector_type="fixture",
            active=True,
            metadata={},
        )
        document = Document(
            document_id="document-1",
            source_id=source.source_id,
            canonical_url="https://example.invalid/source-document",
            title="Source document",
            metadata={},
        )
        version = DocumentVersion(
            document_version_id="version-1",
            document_id=document.document_id,
            content_hash="hash-1",
            metadata={},
        )
        candidate = _candidate("candidate-1", "needs_review")
        evidence = [
            Evidence(
                evidence_id=f"evidence-{field}",
                document_version_id=version.document_version_id,
                strategy_candidate_id=candidate.candidate_id,
                field_name=field,
                quote=f"Evidence for {field}.",
                section_or_locator=f"{field} section",
                captured_at=datetime(2026, 8, 2, tzinfo=UTC),
            )
            for field in CORE_FIELDS
        ]
        session.add_all([source, document, version, candidate, *evidence])
        session.commit()

        view = ReviewService(session).get_candidate(candidate.candidate_id)

        assert view is not None
        assert view["source_link"] == document.canonical_url
        assert view["document_version_id"] == version.document_version_id
        assert view["document_version"]["document_version_id"] == version.document_version_id
        assert all(
            item["evidence"][0]["document_version_id"] == version.document_version_id
            for item in view["items"]
        )
        assert {item["evidence"][0]["quote"] for item in view["items"]} == {
            f"Evidence for {field}." for field in CORE_FIELDS
        }
    finally:
        session.close()
        engine.dispose()


def test_list_candidates_filters_by_review_status() -> None:
    engine, session = _database()
    try:
        session.add_all(
            [
                _candidate("candidate-needs-review", "needs_review"),
                _candidate("candidate-approved", "approved"),
                _candidate("candidate-rejected", "rejected"),
            ]
        )
        session.commit()

        candidates = ReviewService(session).list_candidates(status="approved")

        assert [candidate.candidate_id for candidate in candidates] == [
            "candidate-approved"
        ]
        assert all(candidate.review_status == "approved" for candidate in candidates)
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("decision", ["approved", "rejected", "archived"])
def test_record_decision_appends_review_and_transitions_candidate(
    decision: str,
) -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-decision", "needs_review")
        session.add(candidate)
        session.commit()

        review = ReviewService(session).record_decision(
            candidate.candidate_id,
            "reviewer-1",
            decision,
            "Reviewed against source evidence.",
        )

        assert isinstance(review, Review)
        assert review.review_id
        assert review.strategy_candidate_id == candidate.candidate_id
        assert review.reviewer_id == "reviewer-1"
        assert review.decision == decision
        assert review.comment == "Reviewed against source evidence."
        assert review.reviewed_at is not None
        assert candidate.review_status == decision
        assert review in candidate.reviews
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize("reviewer_id", [None, "", "   ", "\t\n"])
def test_record_decision_rejects_missing_reviewer_without_creating_review(
    reviewer_id: str | None,
) -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-reviewer", "needs_review")
        session.add(candidate)
        session.commit()

        with pytest.raises(ValueError, match="reviewer_id"):
            ReviewService(session).record_decision(
                candidate.candidate_id,
                reviewer_id,  # type: ignore[arg-type]
                "approved",
            )

        assert session.query(Review).count() == 0
        assert candidate.review_status == "needs_review"
    finally:
        session.close()
        engine.dispose()


def test_record_decision_delegates_every_transition_to_state_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-transition", "needs_review")
        session.add(candidate)
        session.commit()
        original_transition = state_machine.transition
        calls: list[tuple[object, object]] = []

        def observed_transition(current: object, target: object) -> object:
            calls.append((current, target))
            return original_transition(current, target)

        monkeypatch.setattr(state_machine, "transition", observed_transition)

        ReviewService(session).record_decision(
            candidate.candidate_id,
            "reviewer-1",
            "approved",
        )

        assert calls == [("needs_review", "approved")]
        assert candidate.review_status == "approved"
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "source,decision",
    [
        ("pending", "approved"),
        ("approved", "rejected"),
        ("needs_review", "pending"),
    ],
)
def test_record_decision_rejects_unlisted_state_transitions(
    source: str,
    decision: str,
) -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-invalid-transition", source)
        session.add(candidate)
        session.commit()

        with pytest.raises(state_machine.InvalidTransition):
            ReviewService(session).record_decision(
                candidate.candidate_id,
                "reviewer-1",
                decision,
            )

        assert session.query(Review).count() == 0
        assert candidate.review_status == source
    finally:
        session.close()
        engine.dispose()


def test_amendment_appends_history_without_overwriting_source_value() -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-amendment", "needs_review")
        source_value = candidate.entry_logic
        session.add(candidate)
        session.commit()

        amendment = ReviewService(session).amend_field(
            candidate.candidate_id,
            "reviewer-1",
            "entry_logic",
            "Enter only after two consecutive confirmations.",
            "Clarify the confirmation requirement.",
        )
        session.commit()
        session.refresh(candidate)

        assert candidate.entry_logic == source_value
        assert candidate.metadata_json["review_amendments"] == [amendment]
        assert set(amendment) == {
            "amended_at",
            "reviewer_id",
            "field_name",
            "previous_value",
            "proposed_value",
            "reason",
        }
        assert amendment["reviewer_id"] == "reviewer-1"
        assert amendment["field_name"] == "entry_logic"
        assert amendment["previous_value"] == source_value
        assert amendment["proposed_value"] == (
            "Enter only after two consecutive confirmations."
        )
        assert amendment["reason"] == "Clarify the confirmation requirement."
        assert amendment["amended_at"]
    finally:
        session.close()
        engine.dispose()


def test_two_amendments_accumulate_in_order() -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-amendment-order", "needs_review")
        source_value = candidate.risk_notes
        session.add(candidate)
        session.commit()

        service = ReviewService(session)
        first = service.amend_field(
            candidate.candidate_id,
            "reviewer-1",
            "risk_notes",
            "First proposed risk note.",
            "First review pass.",
        )
        second = service.amend_field(
            candidate.candidate_id,
            "reviewer-2",
            "risk_notes",
            "Second proposed risk note.",
            "Second review pass.",
        )
        session.commit()
        session.refresh(candidate)

        assert candidate.risk_notes == source_value
        assert candidate.metadata_json["review_amendments"] == [first, second]
        assert [entry["reviewer_id"] for entry in candidate.metadata_json["review_amendments"]] == [
            "reviewer-1",
            "reviewer-2",
        ]
        assert [
            entry["previous_value"]
            for entry in candidate.metadata_json["review_amendments"]
        ] == [source_value, source_value]
    finally:
        session.close()
        engine.dispose()


def test_amendment_rejects_field_outside_core_fields() -> None:
    engine, session = _database()
    try:
        candidate = _candidate("candidate-invalid-amendment", "needs_review")
        session.add(candidate)
        session.commit()

        with pytest.raises(ValueError, match="field_name"):
            ReviewService(session).amend_field(
                candidate.candidate_id,
                "reviewer-1",
                "canonical_name",
                "Not allowed",
                "Only core fields can be amended.",
            )

        assert candidate.metadata_json == {}
    finally:
        session.close()
        engine.dispose()
