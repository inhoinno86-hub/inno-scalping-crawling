"""Build and archive one idempotent briefing execution.

This module is intentionally an orchestration boundary.  Collection is owned
by the existing pipeline; this function combines its persisted run records
with the review queue, the Phase 3 cursor, and the existing Markdown renderer.
No new persistence fields or configuration keys are introduced here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import UTC, date, datetime
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from scalping_briefing.models import (
    Briefing,
    BriefingItem,
    CollectionJob,
    DocumentVersion,
    Evidence,
    Source,
    StrategyCandidate,
)
from scalping_briefing.models.base import new_id, utc_now
from scalping_briefing.pipeline import briefing_cursor, schedule

from . import briefing_render


_FINAL_REVIEW_STATES = frozenset({"approved", "rejected", "archived"})
_EXCLUDED_QUEUE_STATES = frozenset({"rejected", "archived"})
_QUEUE_REVIEW_STATES = frozenset({"pending", "needs_review"})
_CANDIDATE_FIELDS = (
    "candidate_id",
    "strategy_id",
    "canonical_name",
    "aliases",
    "summary",
    "asset_classes",
    "market_types",
    "strategy_families",
    "holding_horizon",
    "microstructure_level",
    "tags",
    "core_hypothesis",
    "core_hypothesis_status",
    "signal_inputs",
    "signal_inputs_status",
    "entry_logic",
    "entry_logic_status",
    "exit_logic",
    "exit_logic_status",
    "required_data",
    "required_data_status",
    "required_frequency",
    "risk_notes",
    "risk_notes_status",
    "field_status",
    "relevance_status",
    "review_status",
    "source_confidence",
    "extraction_confidence",
    "value_score",
    "value_score_breakdown",
    "novelty_status",
    "related_strategy_ids",
    "document_version_ids",
    "metadata_json",
)
_EVIDENCE_FIELDS = (
    "evidence_id",
    "document_version_id",
    "strategy_candidate_id",
    "field_name",
    "quote",
    "section_or_locator",
    "captured_at",
    "source_url",
    "metadata_json",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _setting(settings: Any, name: str, default: Any) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _int_setting(settings: Any, name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(_setting(settings, name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _same_instant(left: Any, right: datetime) -> bool:
    parsed = _as_datetime(left)
    return parsed is not None and parsed == right.astimezone(UTC)


def _candidate_timestamp(candidate: Any) -> datetime | None:
    """Return the best persisted timestamp for window membership."""

    direct = _as_datetime(_field(candidate, "created_at"))
    if direct is not None:
        return direct
    metadata = _field(candidate, "metadata_json", _field(candidate, "metadata", {}))
    if isinstance(metadata, Mapping):
        for key in ("created_at", "collected_at", "published_at", "retrieved_at"):
            timestamp = _as_datetime(metadata.get(key))
            if timestamp is not None:
                return timestamp
    for evidence in _records(_field(candidate, "evidence", [])):
        timestamp = _as_datetime(_field(evidence, "captured_at"))
        if timestamp is not None:
            return timestamp
        version = _field(evidence, "document_version")
        timestamp = _as_datetime(_field(version, "retrieved_at"))
        if timestamp is not None:
            return timestamp
    return None


def _records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes, bytearray)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    try:
        return list(value)
    except TypeError:
        return [value]


def _in_window(candidate: Any, window_start: datetime, window_end: datetime) -> bool:
    timestamp = _candidate_timestamp(candidate)
    if timestamp is None:
        # A candidate without a timestamp is still a queue record.  The
        # existing model has no separate collection timestamp, so do not lose
        # it merely because an older database row lacks optional metadata.
        return True
    return window_start <= timestamp <= window_end


def _evidence_records(candidate: Any) -> list[Any]:
    return sorted(
        _records(_field(candidate, "evidence", [])),
        key=lambda item: (
            str(_field(item, "field_name", "")),
            str(_field(item, "evidence_id", "")),
        ),
    )


def _document_link(evidence: Any) -> str | None:
    for name in ("source_url", "source_link", "original_url", "canonical_url"):
        value = _field(evidence, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    version = _field(evidence, "document_version")
    document = _field(version, "document")
    for owner in (document, version):
        for name in ("original_url", "canonical_url", "source_url"):
            value = _field(owner, name)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _evidence_mapping(evidence: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _EVIDENCE_FIELDS:
        value = _field(evidence, name, None)
        if name == "metadata_json":
            name = "metadata"
        if value is not None:
            result[name] = deepcopy(value)
    link = _document_link(evidence)
    if link:
        result.setdefault("source_url", link)
        result.setdefault("source_link", link)
    version = _field(evidence, "document_version")
    if version is not None:
        result["document_version_id"] = _field(
            version, "document_version_id", result.get("document_version_id")
        )
    return result


def _candidate_mapping(
    candidate: Any,
    *,
    carried_over: bool,
    reason_included: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _CANDIDATE_FIELDS:
        value = _field(candidate, name, None)
        output_name = "metadata" if name == "metadata_json" else name
        if value is not None:
            result[output_name] = deepcopy(value)
    evidence = [_evidence_mapping(row) for row in _evidence_records(candidate)]
    result["evidence"] = evidence
    result["carried_over"] = carried_over
    result["reason_included"] = reason_included
    for row in evidence:
        link = row.get("source_url")
        if isinstance(link, str) and link.strip():
            result.setdefault("source_url", link)
            result.setdefault("source_link", link)
            break
    return result


def _candidate_sort_key(candidate: Any) -> tuple[Any, ...]:
    score = _field(candidate, "value_score")
    try:
        score_key = -float(score) if score is not None else float("inf")
    except (TypeError, ValueError):
        score_key = float("inf")
    created = _candidate_timestamp(candidate) or datetime.min.replace(tzinfo=UTC)
    return (score_key, created, str(_field(candidate, "candidate_id", "")))


def _load_previous_briefings(session: Session, scheduled_for: datetime) -> list[Briefing]:
    options = (
        selectinload(Briefing.items)
        .selectinload(BriefingItem.strategy_candidate)
        .selectinload(StrategyCandidate.evidence)
        .selectinload(Evidence.document_version)
        .selectinload(DocumentVersion.document),
    )
    rows = list(session.scalars(select(Briefing).options(*options)).all())
    current = scheduled_for.astimezone(UTC)
    return sorted(
        [
            row
            for row in rows
            if _as_datetime(_field(row, "scheduled_for")) is not None
            and _as_datetime(_field(row, "scheduled_for")) < current
        ],
        key=lambda row: (
            _as_datetime(_field(row, "scheduled_for")) or datetime.min.replace(tzinfo=UTC),
            str(_field(row, "briefing_id", "")),
        ),
    )


def _load_candidates(session: Session) -> list[StrategyCandidate]:
    options = (
        selectinload(StrategyCandidate.evidence)
        .selectinload(Evidence.document_version)
        .selectinload(DocumentVersion.document),
    )
    return list(
        session.scalars(select(StrategyCandidate).options(*options)).all()
    )


def _source_summary(session: Session, scheduled_for: datetime) -> dict[str, Any]:
    """Summarize persisted collection jobs without executing collection."""

    supplied = getattr(session, "info", {}).get("source_summary")
    if isinstance(supplied, Mapping):
        summary = dict(supplied)
        summary.setdefault("total", 0)
        summary.setdefault("success", 0)
        summary.setdefault("failed", 0)
        summary.setdefault("not_executed", 0)
        return summary

    jobs: list[Any] = []
    try:
        jobs = list(session.scalars(select(CollectionJob)).all())
    except Exception:
        # A caller may use a pre-Phase-3 database containing no job table.  A
        # briefing remains useful; source counts then fall back to registry rows.
        jobs = []
    matching_jobs = [
        job
        for job in jobs
        if _same_instant(_field(job, "scheduled_for"), scheduled_for)
    ]
    if matching_jobs:
        success = sum(_field(job, "status") in {"success", "succeeded", "collected"} for job in matching_jobs)
        failed = sum(_field(job, "status") in {"failed", "failure", "error"} for job in matching_jobs)
        not_executed = len(matching_jobs) - success - failed
        summary: dict[str, Any] = {
            "total": len(matching_jobs),
            "success": success,
            "failed": failed,
            "not_executed": not_executed,
            "jobs": [
                {
                    "source_id": _field(job, "source_id"),
                    "status": _field(job, "status"),
                    "error": _field(job, "error"),
                }
                for job in matching_jobs
            ],
        }
        return summary

    try:
        active_sources = list(
            session.scalars(select(Source).where(Source.active.is_(True))).all()
        )
    except Exception:
        active_sources = []
    return {
        "total": len(active_sources),
        "success": 0,
        "failed": 0,
        "not_executed": len(active_sources),
        "sources": [
            {"source_id": _field(source, "source_id"), "status": "not_executed"}
            for source in active_sources
        ],
    }


def _archive_markdown(briefing_id: str, markdown: str) -> str:
    archive_dir = Path("storage") / "briefings"
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{briefing_id}.md"
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=archive_dir, delete=False, prefix=f".{briefing_id}."
    ) as handle:
        temporary = Path(handle.name)
        handle.write(str(markdown))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    return str(target)


def _notes(
    *,
    candidate_count: int,
    approved_count: int,
    failed_sources: int,
    carried_count: int,
) -> list[str]:
    notes: list[str] = []
    if candidate_count > approved_count or carried_count:
        notes.append("승인 대기")
    if approved_count == 0:
        notes.append("적격 신규 자료 없음")
    if failed_sources:
        notes.append("일부 출처 수집 실패")
    return notes


def build_briefing(
    session: Session,
    *,
    scheduled_for: datetime,
    trigger_type: str,
    settings: Any,
    run_attempt: int = 1,
) -> Briefing:
    """Build, persist, render, and archive one briefing execution.

    The schedule trigger is the idempotency boundary.  Re-entering the same
    ``(scheduled_for, trigger_type)`` updates that row and increments its
    attempt instead of inserting another ``Briefing``.
    """

    if isinstance(run_attempt, bool) or int(run_attempt) < 1:
        raise ValueError("run_attempt must be at least 1")
    scheduled = schedule.schedule_trigger(
        scheduled_for,
        trigger_type=trigger_type,
    )
    briefing_id = scheduled["briefing_id"]
    occurrence = scheduled["scheduled_for"]
    previous = _load_previous_briefings(session, occurrence)
    initial_lookback = _int_setting(settings, "initial_lookback_days", 14)
    max_lookback = _int_setting(settings, "max_lookback_days", 30, minimum=1)
    cursor = briefing_cursor.advance_cursor(
        previous,
        scheduled_for=occurrence,
        run_status="success",
        initial_lookback_days=initial_lookback,
        max_lookback_days=max_lookback,
    )

    candidates = _load_candidates(session)
    current_candidates = [
        candidate
        for candidate in candidates
        if _field(candidate, "review_status", "pending") not in _EXCLUDED_QUEUE_STATES
        and _in_window(candidate, cursor.window_start, cursor.window_end)
    ]
    current_approved = [
        candidate
        for candidate in current_candidates
        if _field(candidate, "review_status") == "approved"
    ]

    # Carry only queue records that were actually represented in an earlier
    # briefing.  This deliberately does not use the current time window, so a
    # delayed approval does not cause interval recollection.
    carried: list[StrategyCandidate] = []
    carried_ids: set[str] = set()
    for old_briefing in reversed(previous):
        for old_item in _records(_field(old_briefing, "items", [])):
            candidate = _field(old_item, "strategy_candidate")
            candidate_id = _field(old_item, "strategy_candidate_id")
            if candidate is None and candidate_id:
                candidate = next(
                    (
                        row
                        for row in candidates
                        if str(_field(row, "candidate_id")) == str(candidate_id)
                    ),
                    None,
                )
            if candidate is None:
                continue
            identifier = str(_field(candidate, "candidate_id", ""))
            if not identifier or identifier in carried_ids:
                continue
            if _field(candidate, "review_status", "pending") in _FINAL_REVIEW_STATES:
                continue
            carried.append(candidate)
            carried_ids.add(identifier)

    current_approved.sort(key=_candidate_sort_key)
    current_pending = [
        candidate
        for candidate in current_candidates
        if _field(candidate, "review_status") in _QUEUE_REVIEW_STATES
    ]
    current_pending.sort(key=_candidate_sort_key)
    carried.sort(key=_candidate_sort_key)

    candidate_order: list[tuple[StrategyCandidate, bool, str]] = []
    seen: set[str] = set()
    for candidate in [*current_approved, *current_pending, *carried]:
        identifier = str(_field(candidate, "candidate_id", ""))
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        status = _field(candidate, "review_status", "pending")
        if status == "approved":
            reason = "승인된 후보"
        elif identifier in carried_ids:
            reason = "승인 대기 · 이전 브리핑에서 이월"
        else:
            reason = "승인 대기"
        candidate_order.append((candidate, identifier in carried_ids, reason))

    candidate_records: list[dict[str, Any]] = []
    skipped_candidates: list[str] = []
    for candidate, carried_over, reason in candidate_order:
        record = _candidate_mapping(
            candidate,
            carried_over=carried_over,
            reason_included=reason,
        )
        # The existing renderer/gate is the authority for Evidence safety.  A
        # malformed queue record stays in review rather than failing a whole
        # scheduled execution.
        if not record.get("evidence"):
            skipped_candidates.append(str(_field(candidate, "candidate_id", "")))
            continue
        candidate_records.append(record)

    report_candidate_ids = {
        str(_field(candidate, "candidate_id", "")) for candidate in current_candidates
    }
    report_candidate_ids.update(carried_ids)
    current_count = len(report_candidate_ids)
    approved_count = len(current_approved)
    source_summary = _source_summary(session, occurrence)
    failed_sources = int(source_summary.get("failed", 0) or 0)
    carried_count = len(carried)
    notes = _notes(
        candidate_count=current_count,
        approved_count=approved_count,
        failed_sources=failed_sources,
        carried_count=carried_count,
    )
    if skipped_candidates:
        notes.append("승인 대기")
        source_summary["skipped_candidates"] = skipped_candidates
    source_summary["notes"] = list(dict.fromkeys(notes))
    source_summary["publication_policy"] = "manual_approval"

    payload = {
        "briefing_id": briefing_id,
        "scheduled_for": occurrence,
        "trigger_type": trigger_type,
        "run_attempt": int(run_attempt),
        "window_start": cursor.window_start,
        "window_end": cursor.window_end,
        "window_truncated": cursor.window_truncated,
        "truncated_from": cursor.truncated_from,
        "run_status": "success",
        "publication_status": "pending_approval",
        "generated_at": utc_now(),
        "timezone": _setting(settings, "TIMEZONE", "Asia/Seoul"),
        "source_summary": source_summary,
        "candidate_count": current_count,
        "approved_count": approved_count,
        "items": candidate_records,
    }
    markdown = briefing_render.render_briefing_markdown(payload, settings=settings)
    markdown_location = _archive_markdown(briefing_id, markdown)
    render_metadata = getattr(markdown, "metadata", {})

    briefing = session.get(Briefing, briefing_id)
    if briefing is None:
        briefing = Briefing(briefing_id=briefing_id)
        session.add(briefing)
        briefing.run_attempt = max(1, int(run_attempt))
    else:
        briefing.run_attempt = max(int(briefing.run_attempt or 1) + 1, int(run_attempt))
        briefing.items.clear()

    briefing.scheduled_for = occurrence
    briefing.trigger_type = trigger_type
    briefing.window_start = cursor.window_start
    briefing.window_end = cursor.window_end
    briefing.window_truncated = cursor.window_truncated
    briefing.run_status = "success"
    briefing.publication_status = "pending_approval"
    briefing.generated_at = payload["generated_at"]
    briefing.timezone = str(payload["timezone"])
    briefing.markdown_location = markdown_location
    briefing.source_summary = source_summary
    briefing.candidate_count = current_count
    briefing.approved_count = approved_count
    briefing.items_truncated = int(render_metadata.get("items_truncated", 0) or 0)

    rendered_limit = _int_setting(settings, "briefing_max_items", 7)
    persisted_records = candidate_records[:rendered_limit]
    for rank, record in enumerate(persisted_records, 1):
        # ``candidate_order`` and records differ only for empty-evidence rows;
        # use the ID lookup to keep ranks and carry-over flags exact.
        candidate_id = record.get("candidate_id")
        selected = next(
            (entry for entry in candidate_order if entry[0].candidate_id == candidate_id),
            None,
        )
        if selected is None:
            continue
        candidate, carried_over, reason = selected
        briefing.items.append(
            BriefingItem(
                briefing_item_id=new_id(),
                strategy_candidate=candidate,
                strategy_id=_field(candidate, "strategy_id"),
                reason_included=reason,
                rank=rank,
                carried_over=carried_over,
                # BriefingItem has the existing two-Evidence persistence
                # contract.  The renderer receives the complete candidate
                # evidence set above and applies the same bound for prose.
                evidence=list(_field(candidate, "evidence", []) or [])[:2],
            )
        )

    session.flush()
    return briefing


__all__ = ["build_briefing"]
