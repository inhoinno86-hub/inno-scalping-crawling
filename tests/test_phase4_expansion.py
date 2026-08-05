from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from scalping_briefing.config import CONFIG_KEYS
from scalping_briefing.ops.expansion import (
    APPENDIX_A_RECALIBRATION_KEYS,
    ExpansionAssessment,
    build_expansion_recommendations,
    evaluate_expansion,
    evaluate_four_week_expansion,
    recommend_threshold_recalibration,
)
from scalping_briefing.ops.metrics import MetricResult, ObservationWindow


START = datetime(2026, 7, 6, tzinfo=UTC)


def _window(number: int) -> ObservationWindow:
    start = START + timedelta(days=number * 7)
    return ObservationWindow(start=start, end=start + timedelta(days=7), timezone="UTC")


def _metrics(
    *,
    verdict: str = "meets_target",
    blocked_metric: str | None = None,
) -> list[MetricResult]:
    metrics: list[MetricResult] = []
    for number in range(1, 7):
        metric_id = f"M{number}"
        current_verdict = verdict if metric_id == blocked_metric else "meets_target"
        value = None if current_verdict == "insufficient_data" else 1
        sample_size = 0 if current_verdict == "insufficient_data" else 1
        metrics.append(
            MetricResult(
                metric_id,
                metric_id,
                value,
                1,
                current_verdict,
                value,
                sample_size,
                sample_size,
            )
        )
    return metrics


def _observation(number: int, **kwargs: object) -> dict[str, object]:
    return {"window": _window(number), "metrics": _metrics(**kwargs)}


def test_four_latest_windows_and_all_six_metrics_are_required() -> None:
    observations = [_observation(0, verdict="breached", blocked_metric="M1")]
    observations.extend(_observation(number) for number in range(1, 5))

    result = evaluate_four_week_expansion(observations)

    assert result.expansion_eligible is True
    assert result.reason == "meets_target"
    assert result.window_ids == tuple(_window(number).window_id for number in range(1, 5))
    assert result.blocked_metrics == ()


def test_insufficient_window_count_blocks_with_window_and_metric_evidence() -> None:
    result = evaluate_four_week_expansion([_observation(0), _observation(1), _observation(2)])

    assert result.expansion_eligible is False
    assert result.reason == "insufficient_data"
    assert result.windows_evaluated == 3
    assert result.blocked_windows
    assert all(blocker.reason == "insufficient_data" for blocker in result.blocked_metrics)
    assert all(blocker.window_id and blocker.metric_id for blocker in result.blocked_metrics)
    assert "insufficient_data" == result.as_dict()["reason"]


def test_insufficient_data_takes_priority_and_breach_is_explicit() -> None:
    result = evaluate_four_week_expansion(
        [
            _observation(0),
            _observation(1, verdict="insufficient_data", blocked_metric="M2"),
            _observation(2, verdict="breached", blocked_metric="M4"),
            _observation(3),
        ]
    )

    assert result.expansion_eligible is False
    assert result.reason == "insufficient_data"
    assert {blocker.metric_id for blocker in result.insufficient_data} == {"M2"}
    assert {blocker.metric_id for blocker in result.breached} == {"M4"}
    assert {blocker.window_id for blocker in result.blocked_metrics} == {
        _window(1).window_id,
        _window(2).window_id,
    }


def test_three_expansion_candidates_return_recommendation_or_hold_with_reason() -> None:
    blocked = evaluate_four_week_expansion([_observation(0), _observation(1)])
    held = build_expansion_recommendations(blocked)

    assert set(held) == {"auto_publish", "real_source_activation", "search_ui"}
    assert all(item["recommendation"] == "hold" for item in held.values())
    assert all(item["reason"] for item in held.values())

    eligible = evaluate_four_week_expansion([_observation(number) for number in range(4)])
    source_policy = {
        "sources": [
            {"source_id": "real-example", "fixture": False, "active": False},
        ]
    }
    recommended = build_expansion_recommendations(eligible, source_policy)

    assert all(item["recommendation"] == "recommend" for item in recommended.values())
    assert "Source Policy" in recommended["real_source_activation"]["reason"]


def test_recalibration_covers_only_appendix_a_phase4_values_without_mutation() -> None:
    default_path = Path("config/default.toml")
    env_path = Path(".env.example")
    before_default = default_path.read_bytes()
    before_env = env_path.read_bytes()
    before_keys = tuple(CONFIG_KEYS)

    result = recommend_threshold_recalibration(
        evaluate_four_week_expansion([_observation(number) for number in range(4)])
    )

    assert tuple(result) == APPENDIX_A_RECALIBRATION_KEYS
    assert set(result) == {
        "initial_lookback_days",
        "max_lookback_days",
        "candidate_score_threshold",
        "briefing_max_items",
        "extraction_confidence_min",
        "max_collect_retries",
    }
    assert all(item["recommendation"] in {"recommend", "hold"} for item in result.values())
    assert all(item["changed"] is False for item in result.values())
    assert default_path.read_bytes() == before_default
    assert env_path.read_bytes() == before_env
    assert tuple(CONFIG_KEYS) == before_keys


def test_combined_assessment_exposes_gate_recommendations_and_recalibration() -> None:
    assessment = evaluate_expansion(
        [_observation(number) for number in range(4)],
        source_policy={"sources": [{"fixture": False, "active": False}]},
    )

    assert isinstance(assessment, ExpansionAssessment)
    assert assessment.expansion_eligible is True
    assert set(assessment.recommendations) == {
        "auto_publish",
        "real_source_activation",
        "search_ui",
    }
    assert set(assessment.recalibration) == set(APPENDIX_A_RECALIBRATION_KEYS)
    assert assessment["expansion_eligible"] is True
