from __future__ import annotations

from datetime import datetime, timedelta, timezone

from scalping_briefing.pipeline.briefing_cursor import advance_cursor


UTC = timezone.utc


def test_missing_cursor_uses_initial_lookback() -> None:
    scheduled_for = datetime(2026, 8, 4, 8, tzinfo=UTC)

    result = advance_cursor([], scheduled_for=scheduled_for, run_status="success")

    assert result.window_start == scheduled_for - timedelta(days=14)
    assert result.window_end == scheduled_for
    assert result.window_truncated is False
    assert result.cursor["window_end"] == scheduled_for


def test_only_successful_execution_advances_cursor() -> None:
    first = datetime(2026, 7, 28, 8, tzinfo=UTC)
    failed_schedule = datetime(2026, 7, 31, 8, tzinfo=UTC)
    next_schedule = datetime(2026, 8, 4, 8, tzinfo=UTC)
    previous_runs = [
        {
            "scheduled_for": first,
            "window_end": first,
            "run_status": "success",
        },
    ]

    failed = advance_cursor(
        previous_runs,
        scheduled_for=failed_schedule,
        run_status="failed",
    )
    resumed = advance_cursor(
        [*previous_runs, {"scheduled_for": failed_schedule, "run_status": "failed"}],
        scheduled_for=next_schedule,
        run_status="success",
    )

    assert failed.advanced is False
    assert failed.cursor["window_end"] == first
    assert resumed.window_start == first
    assert resumed.window_end == next_schedule


def test_old_success_window_is_truncated_using_collection_window_contract() -> None:
    scheduled_for = datetime(2026, 8, 10, 8, tzinfo=UTC)
    old_success = scheduled_for - timedelta(days=40)

    result = advance_cursor(
        [{"window_end": old_success, "run_status": "success"}],
        scheduled_for=scheduled_for,
        run_status="success",
    )

    expected_start = scheduled_for - timedelta(days=30)
    assert result.window_start == expected_start
    assert result.window_truncated is True
    assert result.truncated_start == expected_start
    assert result.truncated_from == old_success
    assert result.truncation["actual_start"] == expected_start.isoformat()
    assert result.cursor["truncated_start"] == expected_start


def test_failed_first_run_does_not_create_cursor() -> None:
    scheduled_for = datetime(2026, 8, 4, 8, tzinfo=UTC)

    result = advance_cursor(None, scheduled_for=scheduled_for, run_status="failed")

    assert result.advanced is False
    assert result.cursor is None
    assert result.window_start == scheduled_for - timedelta(days=14)
