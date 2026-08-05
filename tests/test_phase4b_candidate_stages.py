from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scalping_briefing.models import Base, Document, DocumentVersion, Source
from scalping_briefing.orchestration import cycle
from scalping_briefing.orchestration.cycle import CycleSummary, run_candidate_stages


SETTINGS = {
    "candidate_score_threshold": 60,
    "extraction_confidence_min": 0.7,
    "quote_max_chars": 300,
}


def _versions() -> list[dict[str, object]]:
    return [
        {
            "document_version_id": "dv-1",
            "processing_status": "deduplicated",
            "normalized_text": "Queue imbalance precedes entry.",
        },
        {
            "document_version_id": "dv-2",
            "processing_status": "deduplicated",
            "normalized_text": "Queue imbalance precedes entry.",
        },
    ]


def _wire_fakes(
    monkeypatch,
    calls: list[tuple[str, str]],
    *,
    fail_extract: str | None = None,
    extraction_state: str = "extracted",
) -> None:
    def classify(document_version, **kwargs):
        assert kwargs["use_llm"] is False
        assert "llm_client" not in kwargs
        identifier = document_version["document_version_id"]
        calls.append((identifier, "classify"))
        document_version["processing_status"] = "extracted"
        return SimpleNamespace(
            status="relevant",
            reason={"decision": "relevant"},
            as_dict=lambda: {"status": "relevant", "reason": {"decision": "relevant"}},
        )

    def extract(document_version, **_kwargs):
        identifier = document_version["document_version_id"]
        calls.append((identifier, "extract"))
        if identifier == fail_extract:
            raise RuntimeError("fixture extraction failure")
        document_version["processing_status"] = extraction_state
        candidate = {
            "candidate_id": f"candidate-{identifier}",
            "review_status": "pending",
            "extraction_confidence": 0.8,
        }
        evidence = [
            {
                "field_name": "core_hypothesis",
                "quote": "Queue imbalance precedes entry.",
                "section_or_locator": "Fixture",
                "document_version_id": identifier,
            }
        ]
        return SimpleNamespace(
            candidate=candidate,
            evidence=evidence,
            error_class=None,
            validated_payload=candidate if extraction_state == "validated" else None,
        )

    def validate(extracted, **kwargs):
        assert kwargs["quote_max_chars"] == 300
        candidate = extracted.candidate
        calls.append((candidate["candidate_id"].removeprefix("candidate-"), "validate"))
        return SimpleNamespace(
            candidate=candidate,
            evidence=extracted.evidence,
            error_class=None,
            valid=True,
        )

    def link(document_version, candidate_id, quotes, **_kwargs):
        calls.append((document_version["document_version_id"], "evidence"))
        assert candidate_id.startswith("candidate-")
        assert quotes
        return quotes

    def score(candidate, document_version, existing_candidates, **_kwargs):
        calls.append((document_version["document_version_id"], "score"))
        assert existing_candidates == []
        candidate["value_score"] = 80
        return SimpleNamespace(candidate=candidate, value_score=80)

    def novelty(candidate, existing_candidates, **_kwargs):
        calls.append((candidate["candidate_id"].removeprefix("candidate-"), "novelty"))
        assert existing_candidates == []
        candidate["novelty_status"] = "new"
        return SimpleNamespace(novelty_status="new")

    def route(candidate, document_version, **_kwargs):
        calls.append((document_version["document_version_id"], "route"))
        candidate["review_status"] = "needs_review"
        return SimpleNamespace(candidate=candidate, processing_status="needs_review")

    monkeypatch.setattr(cycle, "classify_document", classify)
    monkeypatch.setattr(cycle, "extract_strategy_candidate", extract)
    monkeypatch.setattr(cycle, "validate_extracted_candidate", validate)
    monkeypatch.setattr(cycle, "link_evidence", link)
    monkeypatch.setattr(cycle, "score_candidate", score)
    monkeypatch.setattr(cycle, "classify_novelty", novelty)
    monkeypatch.setattr(cycle, "route_candidate", route)


def test_candidate_stages_run_in_order_for_each_document(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls)
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        _versions(),
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
        now=None,
    )

    expected_stages = (
        "classify",
        "extract",
        "validate",
        "evidence",
        "score",
        "novelty",
        "route",
    )
    assert [stage for _identifier, stage in calls] == list(expected_stages) * 2
    assert len(routed) == 2
    assert summary.failures == []
    assert all(summary.stages[name].to_payload() == {"processed": 2, "succeeded": 2, "failed": 0, "skipped": 0} for name in expected_stages)
    assert all(result.candidate["review_status"] == "needs_review" for result in routed)
    assert all(result.candidate["review_status"] != "approved" for result in routed)


def test_extraction_failure_is_isolated_and_skipped_stages_stay_zero(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls, fail_extract="dv-1")
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        _versions(),
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert len(routed) == 1
    assert summary.stages["extract"].to_payload() == {
        "processed": 2,
        "succeeded": 1,
        "failed": 1,
        "skipped": 0,
    }
    assert summary.stages["validate"].to_payload() == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert summary.stages["route"].processed == 1
    assert len(summary.failures) == 1
    assert summary.failures[0].stage == "extract"
    assert summary.failures[0].identifier == "dv-1"
    assert summary.stages["validate"].processed == 1
    assert summary.stages["briefing"].to_payload() == {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
    }
    assert routed[0].candidate["review_status"] != "approved"


def test_irrelevant_classification_does_not_fabricate_downstream_success(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []

    def classify(document_version, **kwargs):
        calls.append(document_version["document_version_id"])
        assert kwargs["use_llm"] is False
        return SimpleNamespace(status="irrelevant")

    monkeypatch.setattr(cycle, "classify_document", classify)
    summary = CycleSummary()

    run_candidate_stages(
        None,
        [{"document_version_id": "dv-irrelevant"}],
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert calls == ["dv-irrelevant"]
    for stage in ("extract", "validate", "evidence", "score", "novelty", "route"):
        assert summary.stages[stage].to_payload() == {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "skipped": 0,
        }


def test_validated_extraction_uses_payload_without_revalidating(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls, extraction_state="validated")
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        _versions()[:1],
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert [stage for _identifier, stage in calls] == [
        "classify",
        "extract",
        "evidence",
        "score",
        "novelty",
        "route",
    ]
    assert summary.stages["validate"].to_payload() == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 0,
    }
    assert summary.failures == []
    assert routed[0].candidate["review_status"] != "approved"


def test_unexpected_post_extraction_state_is_a_validate_failure(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls, extraction_state="needs_review")
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        _versions()[:1],
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert routed == []
    assert ("dv-1", "validate") not in calls
    assert summary.stages["validate"].to_payload() == {
        "processed": 1,
        "succeeded": 0,
        "failed": 1,
        "skipped": 0,
    }
    assert summary.failures[0].stage == "validate"
    assert "needs_review" in summary.failures[0].reason


class _RecordingClient:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response

    def complete(self, _prompt: str, **_kwargs: object) -> dict[str, object]:
        return deepcopy(self.response)


def _real_database(tmp_path: Path) -> tuple[object, Session, DocumentVersion]:
    body = (
        "Queue imbalance can precede short-horizon movement. "
        "Queue imbalance and order book are signal inputs. "
        "Enter after the documented queue imbalance condition. "
        "Exit on reversal or the documented holding timeout. "
        "L2 quotes and trades are required data. "
        "Latency and adverse selection require review."
    )
    body_path = tmp_path / "real-candidate.txt"
    body_path.write_text(body, encoding="utf-8")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    source = Source(
        source_id="phase4b-real-source",
        name="Phase 4b real-function fixture source",
        type="fixture",
        base_url="fixture://phase4b-real",
        connector_type="fixture",
        active=True,
        metadata={},
    )
    document = Document(
        document_id="phase4b-real-document",
        source_id=source.source_id,
        canonical_url="https://example.invalid/phase4b/real",
        original_url="https://example.invalid/phase4b/real",
        title="Queue Momentum Fixture",
        collection_status="collected",
        processing_status="collected",
        access_status="allowed",
        robots_allowed=True,
        metadata={},
    )
    version = DocumentVersion(
        document_version_id="phase4b-real-version",
        document_id=document.document_id,
        version_no=1,
        content_hash="sha256:phase4b-real-content",
        body_hash="sha256:phase4b-real-body",
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


def _real_response(version_id: str) -> dict[str, object]:
    evidence = [
        {
            "field_name": "core_hypothesis",
            "quote": "Queue imbalance can precede short-horizon movement.",
            "section_or_locator": "Hypothesis",
            "document_version_id": version_id,
        },
        {
            "field_name": "signal_inputs",
            "quote": "Queue imbalance and order book",
            "section_or_locator": "Signals",
            "document_version_id": version_id,
        },
        {
            "field_name": "entry_logic",
            "quote": "Enter after the documented queue imbalance condition.",
            "section_or_locator": "Entry",
            "document_version_id": version_id,
        },
        {
            "field_name": "exit_logic",
            "quote": "Exit on reversal or the documented holding timeout.",
            "section_or_locator": "Exit",
            "document_version_id": version_id,
        },
        {
            "field_name": "required_data",
            "quote": "L2 quotes and trades",
            "section_or_locator": "Data",
            "document_version_id": version_id,
        },
        {
            "field_name": "risk_notes",
            "quote": "Latency and adverse selection require review.",
            "section_or_locator": "Risks",
            "document_version_id": version_id,
        },
    ]
    return {
        "candidate_id": "phase4b-real-candidate",
        "canonical_name": "Queue Momentum Fixture",
        "summary": "A bounded short-horizon queue candidate.",
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
        "field_status": {
            field: "explicit"
            for field in (
                "core_hypothesis",
                "signal_inputs",
                "entry_logic",
                "exit_logic",
                "required_data",
                "risk_notes",
            )
        },
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.9,
        "extraction_confidence": 0.8,
        "document_version_ids": [version_id],
        "metadata": {"evidence": evidence},
    }


def test_real_candidate_stages_use_in_memory_sqlite_and_never_approve(
    tmp_path,
) -> None:
    engine, session, version = _real_database(tmp_path)
    try:
        settings = SimpleNamespace(
            candidate_score_threshold=60,
            extraction_confidence_min=0.7,
            quote_max_chars=300,
            llm_client=_RecordingClient(_real_response(version.document_version_id)),
        )
        summary = CycleSummary()

        routed = run_candidate_stages(
            session,
            [version],
            settings=settings,
            summary=summary,
            alerts_dir=tmp_path / "alerts",
        )

        assert summary.stages["validate"].to_payload() == {
            "processed": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
        }
        assert summary.stages["route"].to_payload() == {
            "processed": 1,
            "succeeded": 1,
            "failed": 0,
            "skipped": 0,
        }
        assert summary.failures == []
        assert len(routed) == 1
        assert routed[0].candidate["review_status"] != "approved"
        assert version.processing_status in {"needs_review", "rejected"}
    finally:
        session.close()
        engine.dispose()


def test_already_processed_versions_are_skipped_without_failure_or_alert(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls)
    versions = _versions()
    versions[0]["processing_status"] = "background_only"
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        versions,
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
        now=None,
    )

    assert {identifier for identifier, _stage in calls} == {"dv-2"}
    assert len(routed) == 1
    assert summary.failures == []
    assert summary.status == "success"
    assert summary.stages["classify"].to_payload() == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "skipped": 1,
    }
    assert list(tmp_path.glob("*.json")) == []


def test_states_the_classifier_cannot_accept_are_all_skipped(monkeypatch, tmp_path) -> None:
    terminal_states = (
        "failed",
        "irrelevant",
        "background_only",
        "access_denied",
        "duplicate",
        "extracted",
        "validated",
        "needs_review",
        "approved",
        "rejected",
        "archived",
    )
    calls: list[tuple[str, str]] = []
    _wire_fakes(monkeypatch, calls)
    versions = [
        {
            "document_version_id": f"dv-{index}",
            "processing_status": state,
            "normalized_text": "Queue imbalance precedes entry.",
        }
        for index, state in enumerate(terminal_states)
    ]
    summary = CycleSummary()

    routed = run_candidate_stages(
        None,
        versions,
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
        now=None,
    )

    assert calls == []
    assert routed == []
    assert summary.failures == []
    assert summary.status == "success"
    assert summary.stages["classify"].to_payload() == {
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": len(terminal_states),
    }
