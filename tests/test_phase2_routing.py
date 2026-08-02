from __future__ import annotations

from copy import deepcopy

import pytest

from scalping_briefing.pipeline.novelty import apply_novelty, classify_novelty
from scalping_briefing.pipeline.routing import route_candidate


def _candidate(
    candidate_id: str,
    *,
    canonical_name: str = "Queue Momentum",
    strategy_families: list[str] | None = None,
    asset_classes: list[str] | None = None,
    holding_horizon: str = "seconds to minutes",
    entry_logic: str = "Enter after queue imbalance crosses the threshold.",
    document_version_ids: list[str] | None = None,
    strategy_id: str | None = None,
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "strategy_id": strategy_id,
        "canonical_name": canonical_name,
        "strategy_families": strategy_families or ["momentum"],
        "asset_classes": asset_classes or ["crypto"],
        "holding_horizon": holding_horizon,
        "core_hypothesis": "Queue imbalance can precede short-horizon movement.",
        "signal_inputs": ["queue imbalance", "order book"],
        "entry_logic": entry_logic,
        "exit_logic": "Exit on reversal or the holding timeout.",
        "required_data": ["L2 quotes", "trades"],
        "risk_notes": "Latency and adverse selection require review.",
        "document_version_ids": document_version_ids or ["dv-current"],
    }


def _existing(**overrides: object) -> dict[str, object]:
    values = _candidate(
        "candidate-existing",
        strategy_id="strategy-existing",
        document_version_ids=["dv-existing"],
    )
    values.update(overrides)
    return values


def test_novelty_new_has_no_related_strategy() -> None:
    result = classify_novelty(_candidate("candidate-new"), [])

    assert result.novelty_status == "new"
    assert result.related_strategy_ids == ()


def test_novelty_new_evidence_reuses_same_strategy_for_new_version() -> None:
    result = classify_novelty(
        _candidate("candidate-evidence", document_version_ids=["dv-new"]),
        [_existing()],
    )

    assert result.novelty_status == "new_evidence"
    assert result.related_strategy_ids == ("strategy-existing",)


def test_novelty_changed_detects_core_logic_change() -> None:
    result = classify_novelty(
        _candidate(
            "candidate-changed",
            entry_logic="Enter only after imbalance persists for three observations.",
        ),
        [_existing()],
    )

    assert result.novelty_status == "changed"
    assert result.related_strategy_ids == ("strategy-existing",)


def test_novelty_variant_detects_related_profile_with_different_horizon() -> None:
    result = classify_novelty(
        _candidate(
            "candidate-variant",
            canonical_name="Queue Momentum Intraday",
            holding_horizon="minutes to hours",
        ),
        [_existing()],
    )

    assert result.novelty_status == "variant"
    assert result.related_strategy_ids == ("strategy-existing",)


def test_novelty_duplicate_requires_same_normalized_fields_and_version() -> None:
    result = classify_novelty(
        _candidate(
            "candidate-duplicate",
            canonical_name=" queue-momentum ",
            document_version_ids=["dv-existing"],
        ),
        [_existing()],
    )

    assert result.novelty_status == "duplicate"
    assert result.related_strategy_ids == ("strategy-existing",)


def test_novelty_is_deterministic_and_related_ids_are_sorted() -> None:
    candidate = _candidate("candidate-deterministic", document_version_ids=["dv-new"])
    existing = [
        _existing(
            candidate_id="candidate-existing-b",
            strategy_id="strategy-b",
            canonical_name="Queue Momentum Variant",
            holding_horizon="minutes to hours",
        ),
        _existing(
            candidate_id="candidate-existing-a",
            strategy_id="strategy-a",
            canonical_name="Queue Momentum Variant",
            holding_horizon="minutes to hours",
        ),
    ]

    first = classify_novelty(deepcopy(candidate), existing)
    second = classify_novelty(deepcopy(candidate), list(reversed(existing)))

    assert (first.novelty_status, first.related_strategy_ids) == (
        second.novelty_status,
        second.related_strategy_ids,
    )
    assert first.related_strategy_ids == ("strategy-a", "strategy-b")


@pytest.mark.parametrize("candidate_id", ["mapping-a", "mapping-b"])
def test_novelty_result_is_a_small_persistable_contract(candidate_id: str) -> None:
    candidate = _candidate(candidate_id)
    result = classify_novelty(candidate, [])

    assert result.as_dict() == {
        "novelty_status": "new",
        "related_strategy_ids": [],
    }


def test_apply_novelty_writes_only_existing_candidate_fields() -> None:
    candidate = _candidate("candidate-persist")

    returned = apply_novelty(candidate, [])

    assert returned is candidate
    assert candidate["novelty_status"] == "new"
    assert candidate["related_strategy_ids"] == []


def _routing_candidate(
    *,
    value_score: int,
    extraction_confidence: float,
    conflicting_field: str | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    candidate: dict[str, object] = {
        "candidate_id": "candidate-routing",
        "value_score": value_score,
        "extraction_confidence": extraction_confidence,
        "entry_logic_status": "explicit",
        "exit_logic_status": "explicit",
        "required_data_status": "explicit",
        "review_status": "pending",
    }
    if conflicting_field is not None:
        candidate[f"{conflicting_field}_status"] = "conflicting"
    document_version = {
        "document_version_id": "version-routing",
        "processing_status": "validated",
    }
    return candidate, document_version


def test_borderline_score_forces_needs_review() -> None:
    candidate, document_version = _routing_candidate(
        value_score=50,
        extraction_confidence=0.9,
    )

    result = route_candidate(candidate, document_version=document_version)

    assert result.processing_status == "needs_review"
    assert document_version["processing_status"] == "needs_review"
    assert candidate["review_status"] == "needs_review"


def test_low_extraction_confidence_forces_needs_review_regardless_of_score() -> None:
    candidate, document_version = _routing_candidate(
        value_score=100,
        extraction_confidence=0.69,
    )

    result = route_candidate(candidate, document_version=document_version)

    assert result.processing_status == "needs_review"
    assert document_version["processing_status"] == "needs_review"
    assert candidate["review_status"] == "needs_review"


def test_conflicting_core_field_forces_needs_review_regardless_of_score() -> None:
    candidate, document_version = _routing_candidate(
        value_score=100,
        extraction_confidence=0.9,
        conflicting_field="required_data",
    )

    result = route_candidate(candidate, document_version=document_version)

    assert result.processing_status == "needs_review"
    assert document_version["processing_status"] == "needs_review"
    assert candidate["review_status"] == "needs_review"
