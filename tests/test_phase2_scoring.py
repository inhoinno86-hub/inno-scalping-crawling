from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

from scalping_briefing.models import StrategyCandidate
from scalping_briefing.pipeline.scoring import (
    VALUE_SCORE_WEIGHTS,
    score_candidate,
)


AS_OF = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def _candidate() -> dict[str, object]:
    return {
        "candidate_id": "candidate-score-1",
        "canonical_name": "Queue Momentum",
        "summary": "A reproducible short-horizon queue strategy.",
        "core_hypothesis": "Queue imbalance can precede short-horizon movement.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["queue imbalance", "order book"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter when queue imbalance exceeds the documented threshold.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit on reversal or after the documented timeout.",
        "exit_logic_status": "explicit",
        "required_data": ["L2 quotes", "trades"],
        "required_data_status": "explicit",
        "required_frequency": "tick / sub-second",
        "risk_notes": "Latency and adverse selection require review.",
        "risk_notes_status": "explicit",
        "asset_classes": ["crypto"],
        "market_types": ["order book"],
        "strategy_families": ["momentum"],
        "holding_horizon": "seconds to minutes",
        "microstructure_level": "L2",
        "relevance_status": "relevant",
        "review_status": "pending",
        "source_confidence": 0.95,
        "extraction_confidence": 0.9,
        "novelty_status": "new",
        "canonical_url": "https://example.invalid/research/queue-momentum",
        "metadata": {
            "source_type": "official research",
            "author_or_org": "Example Exchange Research",
            "published_at": "2026-08-01T12:00:00+00:00",
            "license": "CC BY 4.0",
            "source_version_ref": "rev-42",
        },
    }


def _document_version() -> dict[str, object]:
    return {
        "document_version_id": "version-score-1",
        "canonical_url": "https://example.invalid/research/queue-momentum",
        "published_at": "2026-08-01T12:00:00+00:00",
        "updated_at": "2026-08-01T12:00:00+00:00",
        "source_version_ref": "rev-42",
    }


def test_value_score_breakdown_persists_all_weighted_criteria_and_reasons() -> None:
    candidate = _candidate()

    result = score_candidate(
        candidate,
        document_version=_document_version(),
        as_of=AS_OF,
    )

    assert set(result.value_score_breakdown) == set(VALUE_SCORE_WEIGHTS)
    assert candidate["value_score"] == result.value_score
    assert candidate["value_score_breakdown"] == result.value_score_breakdown
    assert 0 <= result.value_score <= 100

    for criterion, maximum in VALUE_SCORE_WEIGHTS.items():
        detail = result.value_score_breakdown[criterion]
        assert detail["max_score"] == maximum
        assert 0 <= detail["score"] <= maximum
        assert isinstance(detail["reason"], str)
        assert detail["reason"].strip()

    assert result.value_score == sum(
        detail["score"] for detail in result.value_score_breakdown.values()
    )


def test_value_scoring_is_deterministic_for_identical_input() -> None:
    first = _candidate()
    second = deepcopy(first)

    first_result = score_candidate(
        first,
        document_version=_document_version(),
        as_of=AS_OF,
    )
    second_result = score_candidate(
        second,
        document_version=_document_version(),
        as_of=AS_OF,
    )

    assert first_result.value_score == second_result.value_score
    assert first_result.value_score_breakdown == second_result.value_score_breakdown


def test_value_score_uses_existing_novelty_fields_without_new_storage_contract() -> None:
    candidate = _candidate()
    candidate["novelty_status"] = "duplicate"

    result = score_candidate(
        candidate,
        document_version=_document_version(),
        as_of=AS_OF,
    )

    assert result.value_score_breakdown["novelty"]["score"] == 0
    assert "duplicate" in result.value_score_breakdown["novelty"]["reason"]
    assert set(candidate) >= {"value_score", "value_score_breakdown"}


def test_value_score_persists_on_existing_strategy_candidate_fields() -> None:
    candidate = StrategyCandidate(
        candidate_id="candidate-score-orm-1",
        canonical_name="Queue Momentum",
        summary="A short-horizon strategy.",
        source_confidence=0.9,
        metadata={
            "source_type": "official research",
            "published_at": "2026-08-01T12:00:00+00:00",
        },
    )

    result = score_candidate(candidate, as_of=AS_OF)

    assert candidate.value_score == result.value_score
    assert candidate.value_score_breakdown == result.value_score_breakdown
