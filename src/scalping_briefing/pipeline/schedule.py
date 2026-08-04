"""Pure weekly briefing schedule calculations.

The scheduler deliberately has no clock, persistence, or daemon integration.
It turns the configured ``DAY HH:MM`` values into timezone-aware occurrence
timestamps and a deterministic trigger payload for the briefing builder.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from hashlib import sha256
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import ConfigError


class ScheduleError(ConfigError):
    """Raised when a schedule or schedule calculation is invalid."""


_DAY_NAMES = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_DAY_NUMBERS = {name: number for number, name in enumerate(_DAY_NAMES)}
_ENTRY_PATTERN = re.compile(
    r"^(MON|TUE|WED|THU|FRI|SAT|SUN) ([01][0-9]|2[0-3]):([0-5][0-9])$"
)
_TRIGGER_TYPES = {"scheduled", "manual"}

# A parsed slot is ``(datetime.weekday(), datetime.time)``.  Keeping this as a
# plain tuple makes the result easy to serialize and keeps the public contract
# independent from a scheduler class.
ScheduleSlot = tuple[int, time]
ParsedSchedule = list[ScheduleSlot]


def parse_schedule(entries: Iterable[str]) -> ParsedSchedule:
    """Parse configured entries into sorted ``(weekday, time)`` tuples.

    Weekdays use :meth:`datetime.date.weekday` numbering (Monday is ``0``).
    The accepted format is strict so malformed configuration fails before any
    occurrence is calculated.
    """

    if isinstance(entries, (str, bytes)):
        raise ScheduleError("schedule must be an iterable of 'DAY HH:MM' entries")
    try:
        raw_entries = list(entries)
    except TypeError as exc:
        raise ScheduleError("schedule must be an iterable of 'DAY HH:MM' entries") from exc
    if not raw_entries:
        raise ScheduleError("schedule must contain at least one entry")

    parsed: ParsedSchedule = []
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, str):
            raise ScheduleError(f"schedule entry {index} must be a string")
        match = _ENTRY_PATTERN.fullmatch(entry.strip())
        if match is None:
            raise ScheduleError(
                f"invalid schedule entry {entry!r}; expected 'DAY HH:MM'"
            )
        day, hour, minute = match.groups()
        slot = (_DAY_NUMBERS[day], time(int(hour), int(minute)))
        if slot in parsed:
            raise ScheduleError(f"duplicate schedule entry: {entry!r}")
        parsed.append(slot)

    parsed.sort(key=lambda slot: (slot[0], slot[1]))
    return parsed


def _zone(value: str | ZoneInfo) -> ZoneInfo:
    if isinstance(value, ZoneInfo):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ScheduleError("timezone must be a valid IANA timezone name")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"unknown timezone: {value!r}") from exc


def _local_datetime(value: datetime, zone: ZoneInfo, *, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ScheduleError(f"{name} must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _slots(schedule: Iterable[str] | Iterable[ScheduleSlot]) -> ParsedSchedule:
    if isinstance(schedule, (str, bytes)):
        return parse_schedule([schedule])
    raw = list(schedule)
    if not raw:
        return parse_schedule(raw)
    if all(
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], int)
        and isinstance(item[1], time)
        for item in raw
    ):
        parsed = [(item[0], item[1].replace()) for item in raw]
        if any(slot[0] < 0 or slot[0] > 6 for slot in parsed):
            raise ScheduleError("parsed schedule weekdays must be between 0 and 6")
        if len(set(parsed)) != len(parsed):
            raise ScheduleError("schedule contains duplicate entries")
        return sorted(parsed, key=lambda slot: (slot[0], slot[1]))
    return parse_schedule(raw)  # type: ignore[arg-type]


def _occurrence_on(date: Any, slot: ScheduleSlot, zone: ZoneInfo) -> datetime:
    return datetime.combine(date, slot[1], tzinfo=zone)


def next_occurrence(
    after: datetime,
    *,
    schedule: Iterable[str] | Iterable[ScheduleSlot],
    timezone: str | ZoneInfo,
) -> datetime:
    """Return the first configured occurrence strictly after ``after``.

    Naive inputs are interpreted in ``timezone``.  Aware inputs are converted
    to that zone, and the returned value is always timezone-aware in the
    requested zone.
    """

    zone = _zone(timezone)
    moment = _local_datetime(after, zone, name="after")
    slots = _slots(schedule)
    for day_offset in range(8):
        candidate_date = moment.date() + timedelta(days=day_offset)
        for slot in slots:
            if slot[0] != candidate_date.weekday():
                continue
            candidate = _occurrence_on(candidate_date, slot, zone)
            if candidate > moment:
                return candidate
    # A non-empty seven-day schedule must produce a result in this horizon.
    raise ScheduleError("could not calculate the next schedule occurrence")


def occurrences_between(
    start: datetime,
    end: datetime,
    *,
    schedule: Iterable[str] | Iterable[ScheduleSlot],
    timezone: str | ZoneInfo,
) -> list[datetime]:
    """Return occurrences in the half-open interval ``[start, end)``.

    Both bounds are normalized to the requested zone.  Using a half-open
    interval prevents a timestamp at one window's end from being counted again
    as the next window's start.
    """

    zone = _zone(timezone)
    local_start = _local_datetime(start, zone, name="start")
    local_end = _local_datetime(end, zone, name="end")
    if local_end < local_start:
        raise ScheduleError("end must not be before start")
    slots = _slots(schedule)
    result: list[datetime] = []
    current_date = local_start.date()
    last_date = local_end.date()
    while current_date <= last_date:
        for slot in slots:
            if slot[0] != current_date.weekday():
                continue
            candidate = _occurrence_on(current_date, slot, zone)
            if local_start <= candidate < local_end:
                result.append(candidate)
        current_date += timedelta(days=1)
    return result


def _trigger_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ScheduleError("scheduled_for must be a datetime")
    if value.tzinfo is None:
        raise ScheduleError("scheduled_for must be timezone-aware")
    return value


def schedule_trigger(
    scheduled_for: datetime,
    *,
    trigger_type: str,
) -> dict[str, Any]:
    """Build a deterministic trigger payload for one briefing occurrence.

    The trigger type participates in the identifier: a manual run at the same
    wall-clock time is a distinct trigger from the scheduled run, while
    repeated calls for the same pair remain idempotent.  Manual triggers are
    explicitly marked as not contributing to the twice-weekly schedule count.
    """

    occurrence = _trigger_datetime(scheduled_for)
    if trigger_type not in _TRIGGER_TYPES:
        raise ScheduleError(
            "trigger_type must be either 'scheduled' or 'manual'"
        )

    canonical = occurrence.astimezone(datetime_timezone.utc).isoformat(
        timespec="seconds"
    )
    identity = f"{trigger_type}:{canonical}"
    briefing_id = f"briefing-{sha256(identity.encode('utf-8')).hexdigest()}"
    return {
        "briefing_id": briefing_id,
        "scheduled_for": occurrence,
        "trigger_type": trigger_type,
        "counts_toward_weekly_schedule": trigger_type == "scheduled",
    }


__all__ = [
    "ParsedSchedule",
    "ScheduleError",
    "ScheduleSlot",
    "next_occurrence",
    "occurrences_between",
    "parse_schedule",
    "schedule_trigger",
]
