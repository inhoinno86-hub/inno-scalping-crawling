from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from scalping_briefing.config import ConfigError
from scalping_briefing.pipeline.schedule import (
    next_occurrence,
    occurrences_between,
    parse_schedule,
    schedule_trigger,
)


KST = ZoneInfo("Asia/Seoul")
SCHEDULE = parse_schedule(["TUE 08:00", "FRI 08:00"])


def test_parse_schedule_returns_sorted_weekday_time_tuples() -> None:
    assert parse_schedule(["FRI 08:00", "TUE 08:00"]) == [
        (1, time(8, 0)),
        (4, time(8, 0)),
    ]


@pytest.mark.parametrize(
    "entries",
    [
        [],
        ["TUESDAY 08:00"],
        ["TUE 8:00"],
        ["TUE 24:00"],
        ["TUE 08:60"],
        ["TUE 08:00", "TUE 08:00"],
    ],
)
def test_invalid_schedule_is_a_config_error(entries: list[str]) -> None:
    with pytest.raises(ConfigError):
        parse_schedule(entries)


def test_next_two_occurrences_are_tuesday_and_friday_in_kst() -> None:
    after = datetime(2026, 8, 3, 9, 0, tzinfo=KST)  # Monday
    first = next_occurrence(after, schedule=SCHEDULE, timezone="Asia/Seoul")
    second = next_occurrence(first, schedule=SCHEDULE, timezone=KST)

    assert first == datetime(2026, 8, 4, 8, 0, tzinfo=KST)
    assert second == datetime(2026, 8, 7, 8, 0, tzinfo=KST)
    assert first.tzinfo == KST
    assert second.tzinfo == KST


def test_occurrences_between_uses_half_open_kst_interval() -> None:
    start = datetime(2026, 8, 3, 0, 0, tzinfo=KST)
    end = datetime(2026, 8, 8, 0, 0, tzinfo=KST)

    assert occurrences_between(
        start, end, schedule=SCHEDULE, timezone="Asia/Seoul"
    ) == [
        datetime(2026, 8, 4, 8, 0, tzinfo=KST),
        datetime(2026, 8, 7, 8, 0, tzinfo=KST),
    ]


def test_schedule_trigger_is_deterministic_and_manual_is_excluded() -> None:
    scheduled_for = datetime(2026, 8, 4, 8, 0, tzinfo=KST)
    first = schedule_trigger(scheduled_for, trigger_type="scheduled")
    repeat = schedule_trigger(scheduled_for, trigger_type="scheduled")
    manual = schedule_trigger(scheduled_for, trigger_type="manual")

    assert first == repeat
    assert first["briefing_id"] != manual["briefing_id"]
    assert first["counts_toward_weekly_schedule"] is True
    assert manual["counts_toward_weekly_schedule"] is False
