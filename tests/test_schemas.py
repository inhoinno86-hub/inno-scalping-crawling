from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_NAMES = (
    "source",
    "document",
    "document_version",
    "strategy_candidate",
    "evidence",
    "briefing",
    "briefing_item",
    "delivery",
)


def schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8"))


def assert_valid(name: str, instance: dict) -> None:
    Draft202012Validator(schema(name)).validate(instance)


def assert_invalid(name: str, instance: dict) -> None:
    with pytest.raises(Exception):
        Draft202012Validator(schema(name)).validate(instance)


def evidence(evidence_id: str = "e-1", *, quote: str = "bounded quote") -> dict:
    return {
        "evidence_id": evidence_id,
        "document_version_id": "dv-1",
        "strategy_candidate_id": "candidate-1",
        "quote": quote,
        "field_name": "summary",
        "section_or_locator": "Abstract",
        "captured_at": "2026-07-01T00:00:00Z",
    }


def strategy_candidate() -> dict:
    return {
        "candidate_id": "candidate-1",
        "canonical_name": "Queue Momentum",
        "summary": "Short horizon queue observation.",
        "core_hypothesis": "Queue imbalance can precede short-horizon price movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance", "trade flow"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter only after the documented imbalance condition.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit at the documented timeout or reversal condition.",
        "exit_logic_status": "explicit",
        "required_data": ["L2 quotes", "trades"],
        "required_data_status": "explicit",
        "risk_notes": "Execution latency and adverse selection require review.",
        "risk_notes_status": "explicit",
        "field_status": {"summary": "explicit", "entry_logic": "explicit"},
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.9,
        "extraction_confidence": 0.8,
    }


def item() -> dict:
    return {
        "briefing_item_id": "bi-1",
        "briefing_id": "b-1",
        "strategy_candidate_id": "candidate-1",
        "reason_included": "new evidence",
        "rank": 1,
        "carried_over": False,
        "evidence": [evidence()],
    }


def test_all_phase_zero_schemas_load_as_draft_2020_12() -> None:
    assert set(SCHEMA_NAMES) == {path.stem.removesuffix(".schema") for path in (ROOT / "schemas").glob("*.schema.json")}
    for name in SCHEMA_NAMES:
        Draft202012Validator.check_schema(schema(name))


def test_field_status_and_robots_decision_contracts() -> None:
    candidate = strategy_candidate()
    assert_valid("strategy_candidate", candidate)
    invalid_status = copy.deepcopy(candidate)
    invalid_status["field_status"]["summary"] = "guessed"
    assert_invalid("strategy_candidate", invalid_status)

    missing_status = copy.deepcopy(candidate)
    del missing_status["entry_logic_status"]
    assert_invalid("strategy_candidate", missing_status)

    empty_unknown = copy.deepcopy(candidate)
    empty_unknown["entry_logic"] = ""
    empty_unknown["entry_logic_status"] = "unknown"
    assert_valid("strategy_candidate", empty_unknown)

    empty_explicit = copy.deepcopy(empty_unknown)
    empty_explicit["entry_logic_status"] = "explicit"
    assert_invalid("strategy_candidate", empty_explicit)

    empty_signal_inputs = copy.deepcopy(candidate)
    empty_signal_inputs["signal_inputs"] = []
    empty_signal_inputs["signal_inputs_status"] = "unknown"
    assert_valid("strategy_candidate", empty_signal_inputs)


@pytest.mark.parametrize(
    ("value_field", "empty_value"),
    (
        ("core_hypothesis", ""),
        ("signal_inputs", []),
        ("entry_logic", ""),
        ("exit_logic", ""),
        ("required_data", []),
        ("risk_notes", ""),
    ),
)
def test_each_empty_strategy_value_requires_unknown_status(
    value_field: str, empty_value: str | list[str]
) -> None:
    candidate = strategy_candidate()
    status_field = f"{value_field}_status"
    candidate[value_field] = empty_value
    candidate[status_field] = "unknown"
    assert_valid("strategy_candidate", candidate)

    candidate[status_field] = "explicit"
    assert_invalid("strategy_candidate", candidate)

    document = {
        "document_id": "doc-1",
        "source_id": "fixture_rss_blog",
        "canonical_url": "https://example.invalid/doc-1",
        "title": "Fixture document",
        "author_or_org": "Fixture",
        "published_at": "2026-07-01T00:00:00Z",
        "language": "en",
        "document_type": "article",
        "robots_allowed": False,
        "robots_rule_matched": "/private",
        "robots_evaluated_at": "2026-07-01T00:00:00Z",
        "access_decision_reason": "robots disallow",
        "collection_status": "access_denied",
        "processing_status": "access_denied",
        "access_status": "denied",
        "license": "fixture-only",
    }
    assert_valid("document", document)


def test_evidence_requires_document_version_id() -> None:
    assert_valid("evidence", evidence())
    missing = evidence()
    del missing["document_version_id"]
    assert_invalid("evidence", missing)


def test_publishable_item_requires_one_or_two_bounded_evidence_quotes() -> None:
    assert_valid("briefing_item", item())
    no_evidence = item()
    no_evidence["evidence"] = []
    assert_invalid("briefing_item", no_evidence)
    too_many = item()
    too_many["evidence"] = [evidence("e-1"), evidence("e-2"), evidence("e-3")]
    assert_invalid("briefing_item", too_many)
    too_long = item()
    too_long["evidence"][0]["quote"] = "x" * 301
    assert_invalid("briefing_item", too_long)
    missing_version = item()
    del missing_version["evidence"][0]["document_version_id"]
    assert_invalid("briefing_item", missing_version)


def test_briefing_contract_excludes_original_full_text() -> None:
    briefing = {
        "briefing_id": "b-1",
        "scheduled_for": "2026-07-01T08:00:00+09:00",
        "trigger_type": "scheduled",
        "run_attempt": 1,
        "window_start": "2026-06-17T08:00:00+09:00",
        "window_end": "2026-07-01T08:00:00+09:00",
        "window_truncated": False,
        "run_status": "success",
        "publication_status": "pending_approval",
        "generated_at": "2026-07-01T08:00:01+09:00",
        "timezone": "Asia/Seoul",
        "items": [item()],
        "notices": {"safety": "safe", "copyright": "bounded", "investment": "not advice"},
    }
    assert_valid("briefing", briefing)
    assert_invalid("briefing", {**briefing, "full_text": "original document"})


def test_delivery_idempotency_key_shape() -> None:
    delivery = {
        "delivery_id": "delivery-1",
        "briefing_id": "b-1",
        "channel": "telegram",
        "idempotency_key": "b-1:telegram:content-sha256",
        "attempt_no": 1,
        "attempted_at": "2026-07-01T08:00:00Z",
        "status": "pending",
    }
    assert_valid("delivery", delivery)
    assert_invalid("delivery", {**delivery, "idempotency_key": "not-a-key"})
