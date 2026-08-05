"""Read-only calculations for the first Phase 4 operational metrics.

The functions in this module deliberately receive a SQLAlchemy session.  They
only select records and calculate values in Python; they do not own a database
engine, commit a transaction, or mutate ORM state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from statistics import median
import os
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from ..models import (
    Briefing,
    BriefingItem,
    CollectionJob,
    Delivery,
    DocumentVersion,
    Source,
    StrategyCandidate,
)


M1_TARGET_SUCCESS_RATE = 0.95
M2_TARGET_DELAY_MINUTES = 30
M3_TARGET_PENDING_REVIEWS = 20
M4_TARGET_DELIVERY_FAILURE_RATE = 0.02
M5_TARGET_DUPLICATE_RATE = 0.0
M6_TARGET_EVIDENCE_GAP_RATE = 0.0

DEFAULT_TIMEZONE = "Asia/Seoul"
VERDICT_MEETS_TARGET = "meets_target"
VERDICT_BREACHED = "breached"
VERDICT_INSUFFICIENT_DATA = "insufficient_data"


def _window_zone(timezone: str) -> ZoneInfo:
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("timezone must be a non-empty IANA timezone name")
    try:
        return ZoneInfo(timezone)
    except Exception as exc:  # ZoneInfoNotFoundError differs across runtimes.
        raise ValueError(f"unknown timezone: {timezone!r}") from exc


def _aware(value: datetime, *, zone: ZoneInfo) -> datetime:
    """Interpret naive DB timestamps in the observation timezone."""

    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _in_window(value: datetime | None, window: ObservationWindow) -> bool:
    if not isinstance(value, datetime):
        return False
    zone = _window_zone(window.timezone)
    moment = _aware(value, zone=zone)
    start = _aware(window.start, zone=zone)
    end = _aware(window.end, zone=zone)
    return start <= moment < end


def _canonical_datetime(value: datetime, *, zone: ZoneInfo) -> str:
    return _aware(value, zone=zone).astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ObservationWindow:
    """Half-open interval used as the common scope for metric observations."""

    start: datetime
    end: datetime
    timezone: str = DEFAULT_TIMEZONE
    window_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.start, datetime) or not isinstance(self.end, datetime):
            raise TypeError("start and end must be datetime values")
        zone = _window_zone(self.timezone)
        if _aware(self.end, zone=zone) < _aware(self.start, zone=zone):
            raise ValueError("end must not be before start")
        canonical = "|".join(
            (
                _canonical_datetime(self.start, zone=zone),
                _canonical_datetime(self.end, zone=zone),
                self.timezone,
            )
        )
        object.__setattr__(self, "window_id", sha256(canonical.encode("utf-8")).hexdigest())

    @classmethod
    def from_bounds(
        cls,
        start: datetime,
        end: datetime,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> "ObservationWindow":
        """Construct a deterministic observation window from its bounds."""

        return cls(start=start, end=end, timezone=timezone)

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "window_id": self.window_id,
            "timezone": self.timezone,
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


@dataclass(frozen=True, slots=True)
class MetricResult:
    """Structured result shared by operational metric calculations."""

    metric_id: str
    title: str
    value: float | int | None
    target: float | int
    verdict: str
    numerator: float | int | None
    denominator: float | int | None
    sample_size: int
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "detail", dict(self.detail))

    @property
    def meets_target(self) -> bool:
        """Compatibility view that cannot be true for insufficient data."""

        return self.verdict == VERDICT_MEETS_TARGET

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "title": self.title,
            "value": self.value,
            "target": self.target,
            "verdict": self.verdict,
            "meets_target": self.meets_target,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "sample_size": self.sample_size,
            "detail": dict(self.detail),
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


def _result(
    *,
    metric_id: str,
    title: str,
    value: float | int | None,
    target: float | int,
    numerator: float | int | None,
    denominator: float | int | None,
    sample_size: int,
    detail: Mapping[str, Any] | None = None,
    meets: bool | None = None,
) -> MetricResult:
    if sample_size == 0:
        verdict = VERDICT_INSUFFICIENT_DATA
        value = None
        meets = False
    elif meets is None:
        raise ValueError("meets must be supplied for a non-empty metric")
    else:
        verdict = VERDICT_MEETS_TARGET if meets else VERDICT_BREACHED
    return MetricResult(
        metric_id=metric_id,
        title=title,
        value=value,
        target=target,
        verdict=verdict,
        numerator=numerator,
        denominator=denominator,
        sample_size=sample_size,
        detail=detail or {},
    )


def calculate_m1_collection_success_rate(
    session: Session,
    window: ObservationWindow,
) -> MetricResult:
    """Calculate active-source terminal collection success rate (M1)."""

    with session.no_autoflush:
        jobs = session.scalars(
            select(CollectionJob)
            .join(Source, CollectionJob.source_id == Source.source_id)
            .where(Source.active.is_(True))
        ).all()

    terminal_jobs: list[CollectionJob] = []
    for job in jobs:
        if job.status != "success" and job.terminal_error is not True:
            continue
        # completed_at is the terminal observation timestamp.  The fallback
        # keeps manually constructed terminal records measurable when their
        # schedule is the only timestamp populated.
        observed_at = job.completed_at or job.scheduled_for
        if _in_window(observed_at, window):
            terminal_jobs.append(job)

    denominator = len(terminal_jobs)
    numerator = sum(job.status == "success" for job in terminal_jobs)
    value = numerator / denominator if denominator else None
    return _result(
        metric_id="M1",
        title="Active-source collection success rate",
        value=value,
        target=M1_TARGET_SUCCESS_RATE,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator,
        detail={
            "terminal_jobs": denominator,
            "successful_jobs": numerator,
            "window_id": window.window_id,
        },
        meets=value >= M1_TARGET_SUCCESS_RATE if value is not None else False,
    )


def calculate_m2_briefing_delay(
    session: Session,
    window: ObservationWindow,
) -> MetricResult:
    """Calculate maximum successful briefing generation delay (M2)."""

    with session.no_autoflush:
        briefings = session.scalars(
            select(Briefing).where(Briefing.run_status == "success")
        ).all()

    latest_by_id: dict[str, Briefing] = {}
    for briefing in briefings:
        # Keep the semantic predicate local as well as in the SQL query.  It
        # makes the calculation correct for alternate Session implementations
        # that may not evaluate SQL expressions before returning records.
        if briefing.run_status != "success":
            continue
        if not _in_window(briefing.scheduled_for, window):
            continue
        previous = latest_by_id.get(briefing.briefing_id)
        attempt = int(briefing.run_attempt or 0)
        if previous is None or attempt > int(previous.run_attempt or 0):
            latest_by_id[briefing.briefing_id] = briefing

    zone = _window_zone(window.timezone)
    delays: list[float] = []
    for briefing in latest_by_id.values():
        if not isinstance(briefing.generated_at, datetime):
            continue
        scheduled_for = _aware(briefing.scheduled_for, zone=zone)
        generated_at = _aware(briefing.generated_at, zone=zone)
        delays.append((generated_at - scheduled_for).total_seconds() / 60)

    sample_size = len(delays)
    maximum = max(delays) if delays else None
    middle = float(median(delays)) if delays else None
    return _result(
        metric_id="M2",
        title="Briefing execution-to-draft delay",
        value=maximum,
        target=M2_TARGET_DELAY_MINUTES,
        # M2 is a latency metric rather than a rate.  Keep the shared fields
        # populated with its judged maximum over one observation unit; the
        # full sample count remains explicit in sample_size/detail.
        numerator=maximum,
        denominator=1 if sample_size else 0,
        sample_size=sample_size,
        detail={
            "maximum": maximum,
            "median": middle,
            "maximum_minutes": maximum,
            "median_minutes": middle,
            "latest_attempts": sample_size,
            "window_id": window.window_id,
        },
        meets=maximum <= M2_TARGET_DELAY_MINUTES if maximum is not None else False,
    )


def calculate_m5_duplicate_rate(
    session: Session,
    window: ObservationWindow,
) -> MetricResult:
    """Calculate same-document repeated-content version rate (M5)."""

    with session.no_autoflush:
        versions = session.scalars(select(DocumentVersion)).all()

    zone = _window_zone(window.timezone)
    versions_with_dates = [
        version
        for version in versions
        if isinstance(version.created_at, datetime)
    ]
    ordered_versions = sorted(
        versions_with_dates,
        key=lambda version: (
            version.document_id,
            _aware(version.created_at, zone=zone),
            int(version.version_no or 0),
            version.document_version_id,
        ),
    )

    seen_hashes: dict[str, set[str]] = {}
    duplicate_ids: set[str] = set()
    for version in ordered_versions:
        hashes = seen_hashes.setdefault(version.document_id, set())
        if version.content_hash in hashes:
            duplicate_ids.add(version.document_version_id)
        hashes.add(version.content_hash)

    versions_in_window = [
        version for version in versions_with_dates if _in_window(version.created_at, window)
    ]
    denominator = len(versions_in_window)
    numerator = sum(
        version.document_version_id in duplicate_ids for version in versions_in_window
    )
    value = numerator / denominator if denominator else None
    return _result(
        metric_id="M5",
        title="Duplicate document-version rate",
        value=value,
        target=M5_TARGET_DUPLICATE_RATE,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator,
        detail={
            "versions_in_window": denominator,
            "duplicate_versions": numerator,
            "duplicate_version_ids": sorted(
                version.document_version_id
                for version in versions_in_window
                if version.document_version_id in duplicate_ids
            ),
            "window_id": window.window_id,
        },
        meets=value <= M5_TARGET_DUPLICATE_RATE if value is not None else False,
    )


def calculate_m3_review_backlog(
    session: Session,
    window: ObservationWindow,
) -> MetricResult:
    """Calculate the window-end count of candidates needing review (M3)."""

    with session.no_autoflush:
        candidates = session.scalars(select(StrategyCandidate)).all()

    # StrategyCandidate has no history column for a point-in-time status.  The
    # current row state is therefore the snapshot observed at ``window.end``;
    # the full row count remains the sample so an empty backlog is measurable.
    snapshot_candidates = list(candidates)
    pending = sum(
        candidate.review_status == "needs_review"
        for candidate in snapshot_candidates
    )
    sample_size = len(snapshot_candidates)
    return _result(
        metric_id="M3",
        title="Strategy-candidate review backlog",
        value=pending if sample_size else None,
        target=M3_TARGET_PENDING_REVIEWS,
        numerator=pending,
        denominator=1 if sample_size else 0,
        sample_size=sample_size,
        detail={
            "pending_reviews": pending,
            "snapshot_candidates": sample_size,
            "snapshot_at": window.end,
            "window_id": window.window_id,
        },
        meets=pending <= M3_TARGET_PENDING_REVIEWS if sample_size else False,
    )


def _delivery_mode(
    *,
    settings: Any | None = None,
    delivery_mode: str | None = None,
) -> str:
    """Return the effective delivery mode without invoking delivery code."""

    configured: list[Any] = []
    if delivery_mode is not None:
        configured.append(delivery_mode)
    elif isinstance(settings, Mapping):
        configured.append(settings.get("DELIVERY_MODE"))
    elif settings is not None:
        configured.append(getattr(settings, "DELIVERY_MODE", None))
    configured.append(os.environ.get("DELIVERY_MODE"))

    present = [value for value in configured if value is not None]
    if not present:
        return "dry_run"
    for value in present:
        normalized = str(value).strip().lower()
        if normalized != "dry_run":
            return str(value).strip() or "live"
    return "dry_run"


def calculate_m4_delivery_failure_rate(
    session: Session,
    window: ObservationWindow,
    *,
    settings: Any | None = None,
    delivery_mode: str | None = None,
) -> MetricResult:
    """Calculate final delivery failure rate per briefing/channel pair (M4)."""

    with session.no_autoflush:
        deliveries = session.scalars(select(Delivery)).all()

    deliveries_in_window = [
        delivery
        for delivery in deliveries
        if _in_window(delivery.attempted_at, window)
    ]
    final_by_pair: dict[tuple[str, str], Delivery] = {}
    for delivery in deliveries_in_window:
        pair = (delivery.briefing_id, delivery.channel)
        previous = final_by_pair.get(pair)
        attempt = int(delivery.attempt_no or 0)
        if previous is None:
            final_by_pair[pair] = delivery
            continue
        previous_key = (
            int(previous.attempt_no or 0),
            _aware(previous.attempted_at, zone=_window_zone(window.timezone)),
            str(previous.delivery_id),
        )
        current_key = (
            attempt,
            _aware(delivery.attempted_at, zone=_window_zone(window.timezone)),
            str(delivery.delivery_id),
        )
        if current_key > previous_key:
            final_by_pair[pair] = delivery

    denominator = len(final_by_pair)
    numerator = sum(
        delivery.status != "success" for delivery in final_by_pair.values()
    )
    value = numerator / denominator if denominator else None
    mode = _delivery_mode(settings=settings, delivery_mode=delivery_mode)
    return _result(
        metric_id="M4",
        title="Final delivery failure rate",
        value=value,
        target=M4_TARGET_DELIVERY_FAILURE_RATE,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator,
        detail={
            "delivery_mode": mode,
            "DELIVERY_MODE": mode,
            "delivery_pairs": denominator,
            "failed_pairs": numerator,
            "final_attempts": len(final_by_pair),
            "window_id": window.window_id,
        },
        meets=value <= M4_TARGET_DELIVERY_FAILURE_RATE
        if value is not None
        else False,
    )


def calculate_m6_evidence_gap_rate(
    session: Session,
    window: ObservationWindow,
) -> MetricResult:
    """Calculate missing-Evidence rate for publish-target core claims (M6)."""

    with session.no_autoflush:
        items = session.scalars(
            select(BriefingItem)
            .join(Briefing, BriefingItem.briefing_id == Briefing.briefing_id)
            .options(
                joinedload(BriefingItem.briefing),
                selectinload(BriefingItem.evidence),
            )
        ).all()

    publish_target_statuses = {"approved", "published"}
    publish_target_items: list[BriefingItem] = []
    for item in items:
        briefing = item.briefing
        if briefing is None:
            continue
        if briefing.publication_status not in publish_target_statuses:
            continue
        if not _in_window(briefing.scheduled_for, window):
            continue
        if item.core_claim is True:
            publish_target_items.append(item)

    denominator = len(publish_target_items)
    numerator = sum(not item.evidence for item in publish_target_items)
    value = numerator / denominator if denominator else None
    return _result(
        metric_id="M6",
        title="Core-claim Evidence gap rate",
        value=value,
        target=M6_TARGET_EVIDENCE_GAP_RATE,
        numerator=numerator,
        denominator=denominator,
        sample_size=denominator,
        detail={
            "publish_target_core_claims": denominator,
            "missing_evidence": numerator,
            "window_id": window.window_id,
        },
        meets=value <= M6_TARGET_EVIDENCE_GAP_RATE
        if value is not None
        else False,
    )


def compute_all_metrics(
    session: Session,
    window: ObservationWindow,
    *,
    settings: Any | None = None,
    delivery_mode: str | None = None,
) -> list[MetricResult]:
    """Return M1 through M6 in deterministic metric-id order."""

    return [
        calculate_m1_collection_success_rate(session, window),
        calculate_m2_briefing_delay(session, window),
        calculate_m3_review_backlog(session, window),
        calculate_m4_delivery_failure_rate(
            session,
            window,
            settings=settings,
            delivery_mode=delivery_mode,
        ),
        calculate_m5_duplicate_rate(session, window),
        calculate_m6_evidence_gap_rate(session, window),
    ]


# Short, stable aliases make the metric IDs convenient for report code while
# keeping the descriptive functions above as the primary API.
calculate_m1 = calculate_m1_collection_success_rate
calculate_m2 = calculate_m2_briefing_delay
calculate_m3 = calculate_m3_review_backlog
calculate_m4 = calculate_m4_delivery_failure_rate
calculate_m5 = calculate_m5_duplicate_rate
calculate_m6 = calculate_m6_evidence_gap_rate
compute_m1_collection_success_rate = calculate_m1_collection_success_rate
compute_m2_briefing_delay = calculate_m2_briefing_delay
compute_m3_review_backlog = calculate_m3_review_backlog
compute_m4_delivery_failure_rate = calculate_m4_delivery_failure_rate
compute_m5_duplicate_rate = calculate_m5_duplicate_rate
compute_m6_evidence_gap_rate = calculate_m6_evidence_gap_rate
m1_collection_success_rate = calculate_m1_collection_success_rate
m2_briefing_delay = calculate_m2_briefing_delay
m3_review_backlog = calculate_m3_review_backlog
m4_delivery_failure_rate = calculate_m4_delivery_failure_rate
m5_duplicate_rate = calculate_m5_duplicate_rate
m6_evidence_gap_rate = calculate_m6_evidence_gap_rate


__all__ = [
    "DEFAULT_TIMEZONE",
    "M1_TARGET_SUCCESS_RATE",
    "M2_TARGET_DELAY_MINUTES",
    "M3_TARGET_PENDING_REVIEWS",
    "M4_TARGET_DELIVERY_FAILURE_RATE",
    "M5_TARGET_DUPLICATE_RATE",
    "M6_TARGET_EVIDENCE_GAP_RATE",
    "MetricResult",
    "ObservationWindow",
    "VERDICT_BREACHED",
    "VERDICT_INSUFFICIENT_DATA",
    "VERDICT_MEETS_TARGET",
    "calculate_m1",
    "calculate_m1_collection_success_rate",
    "calculate_m2",
    "calculate_m2_briefing_delay",
    "calculate_m3",
    "calculate_m3_review_backlog",
    "calculate_m4",
    "calculate_m4_delivery_failure_rate",
    "calculate_m5",
    "calculate_m5_duplicate_rate",
    "calculate_m6",
    "calculate_m6_evidence_gap_rate",
    "compute_all_metrics",
    "compute_m1_collection_success_rate",
    "compute_m2_briefing_delay",
    "compute_m3_review_backlog",
    "compute_m4_delivery_failure_rate",
    "compute_m5_duplicate_rate",
    "compute_m6_evidence_gap_rate",
    "m1_collection_success_rate",
    "m2_briefing_delay",
    "m3_review_backlog",
    "m4_delivery_failure_rate",
    "m5_duplicate_rate",
    "m6_evidence_gap_rate",
]
