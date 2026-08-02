from __future__ import annotations

import pytest

from scalping_briefing.pipeline.state_machine import (
    ALLOWED_TRANSITIONS,
    ERROR_RETRY_FIELDS,
    TERMINAL_STATES,
    InvalidTransition,
    PipelineState,
    PipelineStateRecord,
    RetryMetadata,
    can_transition,
    transition,
)


def test_happy_path_and_all_terminal_states_match_section_9_1() -> None:
    path = [
        "discovered",
        "collected",
        "normalized",
        "deduplicated",
        "classified",
        "extracted",
        "validated",
        "needs_review",
        "approved",
    ]
    for current, target in zip(path, path[1:]):
        assert can_transition(current, target)
        assert transition(current, target) == target
    assert TERMINAL_STATES == {
        "approved",
        "rejected",
        "archived",
        "duplicate",
        "irrelevant",
        "background_only",
        "access_denied",
        "failed",
    }
    assert all(not ALLOWED_TRANSITIONS[state] for state in TERMINAL_STATES)


@pytest.mark.parametrize(
    "current,target",
    [
        ("discovered", "normalized"),
        ("normalized", "failed"),
        ("validated", "approved"),
        ("approved", "rejected"),
        ("failed", "discovered"),
    ],
)
def test_unlisted_and_terminal_transitions_are_rejected(current: str, target: str) -> None:
    with pytest.raises(InvalidTransition):
        transition(current, target)


def test_error_retry_axis_is_not_a_pipeline_state() -> None:
    assert "retrying" not in {state.value for state in PipelineState}
    assert set(ERROR_RETRY_FIELDS) == {
        "error_class",
        "retry_count",
        "next_retry_at",
        "last_error_at",
        "terminal_error",
    }
    record = PipelineStateRecord("extracted")
    updated = record.with_retry(RetryMetadata(error_class="timeout", retry_count=1))
    assert updated.status == "extracted"
    assert updated.retry_count == 1
    with pytest.raises(InvalidTransition):
        updated.transition_to("approved")
