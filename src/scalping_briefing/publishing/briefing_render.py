"""Render a bounded, traceable strategy briefing as Korean Markdown.

The renderer consumes the existing publication contract instead of reading
documents or source bodies.  Raw candidates are adapted through
``candidate_view.build_candidate_view``; already-built candidate views are
accepted as-is and still pass through the publication gate.  This keeps the
renderer useful at the archive boundary without creating a second Evidence
contract.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from . import candidate_view, gate
from .phrase_lint import lint_text


_MISSING = object()
_CORE_FIELDS = frozenset(
    {"core_hypothesis", "signal_inputs", "entry_logic", "exit_logic", "required_data", "risk_notes"}
)
_DEFAULT_MAX_ITEMS = 7
_DEFAULT_MAX_QUOTE_CHARS = 300


class BriefingMarkdown(str):
    """A ``str`` carrying deterministic render metadata.

    The public renderer remains string-compatible while callers that archive
    output can inspect ``result.metadata`` (or the compatibility alias
    ``result.meta``) for truncation and count information.
    """

    def __new__(cls, value: str, metadata: Mapping[str, Any]) -> "BriefingMarkdown":
        rendered = super().__new__(cls, value)
        rendered.metadata = dict(metadata)
        rendered.meta = rendered.metadata
        return rendered


def _get(value: Any, *names: str, default: Any = None) -> Any:
    """Read the first present field from mappings and ORM-like objects."""

    for name in names:
        if isinstance(value, Mapping):
            if name in value:
                return value[name]
            continue
        try:
            result = getattr(value, name)
        except (AttributeError, KeyError):
            continue
        if result is not None:
            return result
    return default


def _present(value: Any, *names: str) -> Any:
    result = _get(value, *names, default=_MISSING)
    return result


def _records(value: Any) -> list[Any]:
    if value is None or value is _MISSING:
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


def _clean_line(value: Any, *, default: str = "확인 불가") -> str:
    if value is None or value is _MISSING:
        return default
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, Mapping):
        pieces = []
        for key, nested in value.items():
            if nested is None or isinstance(nested, (Mapping, list, tuple, set)):
                nested = _compact_value(nested)
            pieces.append(f"{key}: {nested}")
        return "; ".join(pieces) or default
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_clean_line(item) for item in value) or default
    text = str(value).strip()
    if not text:
        return default
    return " ".join(text.splitlines()).strip() or default


def _compact_value(value: Any) -> str:
    if value is None or value is _MISSING:
        return "확인 불가"
    if isinstance(value, Mapping):
        return "; ".join(f"{key}: {_compact_value(nested)}" for key, nested in value.items())
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_compact_value(item) for item in value) or "확인 불가"
    return _clean_line(value)


def _format_time(value: Any) -> str:
    if value is None or value is _MISSING:
        return "확인 불가"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _clean_line(value)


def _setting(settings: Any, name: str, default: Any) -> Any:
    value = _get(settings, name, default=_MISSING)
    return default if value is _MISSING or value is None else value


def _int_setting(settings: Any, name: str, default: int, *, minimum: int = 0) -> int:
    value = _setting(settings, name, default)
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _payload_section(payload: Any) -> Any:
    nested = _get(payload, "briefing", default=_MISSING)
    return nested if nested is not _MISSING and nested is not None else payload


def _window_value(payload: Any, window: Any, *names: str) -> Any:
    value = _get(payload, *names, default=_MISSING)
    if value is not _MISSING:
        return value
    return _get(window, *names, default=None)


def _source_counts(payload: Any, section: Any) -> tuple[int, int, int, int]:
    summary = _get(section, "source_summary", "sources_summary", "collection_summary", default=_MISSING)
    if summary is _MISSING:
        summary = _get(payload, "source_summary", "sources_summary", "collection_summary", default={})
    source_records = _get(section, "sources", "source_results", "source_statuses", default=_MISSING)
    if source_records is _MISSING:
        source_records = _get(payload, "sources", "source_results", "source_statuses", default=_MISSING)
    if source_records is _MISSING and isinstance(summary, Sequence) and not isinstance(summary, (str, bytes, bytearray)):
        source_records = summary

    records = _records(source_records) if source_records is not _MISSING else []
    totals: dict[str, int] = {"total": 0, "success": 0, "failed": 0, "not_executed": 0}
    summary_keys: set[str] = set()

    def count(value: Any) -> int:
        if value is None or value is _MISSING:
            return 0
        if isinstance(value, (list, tuple, set, Mapping)):
            return len(value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    if isinstance(summary, Mapping):
        nested_counts = _get(summary, "counts", "statuses", default={})
        for key, aliases in {
            "total": ("total", "count", "source_count", "source_total", "sources"),
            "success": ("success", "successful", "succeeded", "success_count", "source_success_count"),
            "failed": ("failed", "failure", "failures", "failed_count", "failure_count", "source_failure_count"),
            "not_executed": (
                "not_executed", "not_executed_count", "not_run", "not_run_count", "unexecuted", "skipped", "skipped_count"
            ),
        }.items():
            value = _get(summary, *aliases, default=_MISSING)
            if value is _MISSING:
                value = _get(nested_counts, *aliases, default=_MISSING)
            else:
                summary_keys.add(key)
            totals[key] = count(value)

    if records:
        record_totals: dict[str, int] = {"total": len(records), "success": 0, "failed": 0, "not_executed": 0}
        for record in records:
            status = _clean_line(_get(record, "status", "run_status", "collection_status", default=""), default="").lower()
            if status in {"success", "succeeded", "ok", "collected"}:
                record_totals["success"] += 1
            elif status in {"failed", "failure", "error"}:
                record_totals["failed"] += 1
            elif status in {"not_executed", "not executed", "not_run", "not run", "skipped", "pending", "unexecuted"}:
                record_totals["not_executed"] += 1
        for key, value in record_totals.items():
            if key not in summary_keys:
                totals[key] = value

    totals["total"] = max(
        totals["total"],
        totals["success"] + totals["failed"] + totals["not_executed"],
    )
    return totals["total"], totals["success"], totals["failed"], totals["not_executed"]


def _item_sources(payload: Any, section: Any) -> list[Any]:
    value = _get(section, "items", "candidate_views", "candidates", default=_MISSING)
    if value is _MISSING:
        value = _get(payload, "items", "candidate_views", "candidates", default=[])
    return _records(value)


def _is_view_record(value: Any) -> bool:
    return (
        _get(value, "evidence", default=_MISSING) is not _MISSING
        and _get(value, "field_name", default=_MISSING) in _CORE_FIELDS
        and _get(value, "candidate_id", "strategy_candidate_id", default=_MISSING) is not _MISSING
    )


def _flatten_view_input(value: Any) -> list[Any]:
    nested = _get(value, "items", default=_MISSING)
    if nested is not _MISSING and not _is_view_record(value):
        result: list[Any] = []
        for entry in _records(nested):
            result.extend(_flatten_view_input(entry))
        return result
    return [value]


def _candidate_and_evidence(value: Any) -> tuple[Any, Any]:
    candidate = _get(value, "candidate", "strategy_candidate", default=value)
    evidence = _get(value, "evidence", default=_MISSING)
    if evidence is _MISSING:
        metadata = _get(candidate, "metadata", "metadata_json", default={})
        evidence = _get(metadata, "evidence", default=[])
    return candidate, evidence


def _build_view_records(values: Sequence[Any], quote_limit: int) -> list[Any]:
    records: list[Any] = []
    for value in values:
        for entry in _flatten_view_input(value):
            if _is_view_record(entry):
                records.append(entry)
                continue
            candidate, evidence = _candidate_and_evidence(entry)
            view = candidate_view.build_candidate_view(candidate, evidence)
            gate.validate_publication(view, max_quote_chars=quote_limit)
            records.extend(_records(_get(view, "items", default=[])))
    if records:
        # Existing views were already gated by candidate_view, but applying the
        # configured lower quote limit here covers both input forms.
        gate.validate_publication({"items": records}, max_quote_chars=quote_limit)
    return records


def _group_records(records: Sequence[Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records:
        identifier = _get(record, "candidate_id", "strategy_candidate_id", "strategy_id", default=None)
        if identifier is None or not str(identifier).strip():
            identifier = _get(record, "briefing_item_id", "canonical_name", default=f"item-{len(order) + 1}")
        key = str(identifier)
        if key not in grouped:
            grouped[key] = dict(record) if isinstance(record, Mapping) else {"record": record}
            grouped[key]["evidence"] = []
            order.append(key)
        target = grouped[key]
        current_evidence = _records(_get(record, "evidence", default=[]))
        target["evidence"].extend(current_evidence)
        if isinstance(record, Mapping):
            for field, value in record.items():
                if field not in target or target[field] is None:
                    target[field] = value
    for key in order:
        evidence: list[Any] = []
        seen: set[tuple[Any, Any]] = set()
        for record in grouped[key].get("evidence", []):
            identity = (
                _get(record, "evidence_id", default=None),
                _get(record, "quote", default=None),
            )
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(record)
        grouped[key]["evidence"] = evidence
    return [grouped[key] for key in order]


def _first(item: Any, *names: str, default: Any = None) -> Any:
    value = _get(item, *names, default=_MISSING)
    if value is not _MISSING and value is not None and value != "":
        return value
    for evidence in _records(_get(item, "evidence", default=[])):
        value = _get(evidence, *names, default=_MISSING)
        if value is not _MISSING and value is not None and value != "":
            return value
        metadata = _get(evidence, "metadata", "metadata_json", default={})
        value = _get(metadata, *names, default=_MISSING)
        if value is not _MISSING and value is not None and value != "":
            return value
    return default


def _url(item: Any) -> str | None:
    value = _first(item, "original_url", "source_url", "source_link", "canonical_url", "original_link")
    if value is None:
        return None
    return _clean_line(value, default="") or None


def _evidence_lines(item: Any, quote_limit: int) -> list[str]:
    lines: list[str] = []
    for evidence in _records(_get(item, "evidence", default=[]))[:2]:
        quote = _get(evidence, "quote", default=None)
        if not isinstance(quote, str) or not quote:
            continue
        if len(quote) > quote_limit:
            raise gate.EvidenceQuoteError(
                f"briefing item evidence quote exceeds {quote_limit} characters"
            )
        document_version_id = _get(evidence, "document_version_id", default=_MISSING)
        locator = _get(evidence, "section_or_locator", "locator", default=_MISSING)
        suffix: list[str] = []
        if document_version_id is not _MISSING and document_version_id:
            suffix.append(f"문서 버전 {document_version_id}")
        if locator is not _MISSING and locator:
            suffix.append(f"위치 {_clean_line(locator)}")
        detail = f" ({'; '.join(suffix)})" if suffix else ""
        lines.append(f"> {quote}" + detail)
    return lines


def _render_item(item: Any, index: int, quote_limit: int) -> list[str]:
    name = _first(item, "canonical_name", "strategy_name", "name", default="확인 불가")
    candidate_id = _first(item, "candidate_id", "strategy_candidate_id", default="확인 불가")
    strategy_id = _first(item, "strategy_id", default=None)
    summary = _first(item, "summary", "one_line_summary", "claim", default="확인 불가")
    strategy_group = _first(item, "strategy_families", "strategy_family", "strategy_groups", default="확인 불가")
    asset_group = _first(item, "asset_classes", "asset_class", "assets", default="확인 불가")
    horizon = _first(item, "holding_horizon", "holding_time_range", "holding_period", default="확인 불가")
    value_score = _first(item, "value_score", "score", default="확인 불가")
    value_reason = _first(
        item,
        "value_score_breakdown",
        "value_score_rationale",
        "value_score_reason",
        "scoring_reason",
        default="확인 불가",
    )
    review_status = _first(item, "review_status", "publication_status", default="확인 불가")
    relationship = _first(
        item,
        "relationship_to_existing",
        "existing_strategy_relationship",
        "existing_strategy_relation",
        "novelty_status",
        default="확인 불가",
    )
    related = _first(item, "related_strategy_ids", "related_strategies", default=None)
    url = _url(item)
    title = _first(item, "original_title", "document_title", "source_title", "title", default=None)
    published = _first(item, "published_at", "published_date", "publication_date", default=None)
    version = _first(item, "source_version_ref", "source_version", "version", default=None)
    document_ids = _first(item, "document_version_ids", default=None)
    evidence_ids = [
        _get(evidence, "evidence_id", default=None)
        for evidence in _records(_get(item, "evidence", default=[]))
        if _get(evidence, "evidence_id", default=None)
    ]
    limitations = _first(item, "limitations", "limitation", "limit_notes", default="확인 불가")
    license_note = _first(item, "license", "license_notes", "licence", default="확인 불가")
    implementation_risk = _first(
        item,
        "execution_risk",
        "implementation_risk",
        "risk_notes",
        "risk_memo",
        default="확인 불가",
    )
    carried_over = _first(item, "carried_over", default=False)
    reason_included = _first(item, "reason_included", default=None)

    lines = [
        f"### {index}. {_clean_line(name)}",
        f"- 한 줄 요약: {_clean_line(summary)}",
        f"- 전략군: {_compact_value(strategy_group)} · 자산군: {_compact_value(asset_group)} · 보유 시간 범위: {_compact_value(horizon)}",
        f"- 가치 점수: {_clean_line(value_score)} · 근거: {_compact_value(value_reason)}",
        f"- 검토 상태: {_clean_line(review_status)} · 기존 전략과의 관계: {_compact_value(relationship)}",
        f"- 후보 ID (`candidate_id`): {_clean_line(candidate_id)}"
        + (f" · 전략 ID (`strategy_id`): {_clean_line(strategy_id)}" if strategy_id else ""),
    ]
    if related:
        lines.append(f"- 관련 기존 전략 ID: {_compact_value(related)}")
    if reason_included:
        lines.append(f"- 포함 사유: {_clean_line(reason_included)}")
    if carried_over:
        lines.append("- 큐 상태: 이전 실행에서 이월된 후보")

    if url:
        label = _clean_line(title, default="원문") if title else "원문"
        lines.append(f"- 원문: [{label}]({url})")
    else:
        lines.append("- 원문 URL: 확인 불가")
    source_details: list[str] = []
    if title:
        source_details.append(f"원문 제목 {_clean_line(title)}")
    if published:
        source_details.append(f"게시일 {_format_time(published)}")
    if version:
        source_details.append(f"버전 {_clean_line(version)}")
    if source_details:
        lines.append(f"- 원문 메타데이터: {' · '.join(source_details)}")
    if document_ids:
        lines.append(f"- 문서 버전 ID (`document_version_id`): {_compact_value(document_ids)}")
    if evidence_ids:
        lines.append(f"- Evidence ID: {_compact_value(evidence_ids)}")
    quote_lines = _evidence_lines(item, quote_limit)
    if quote_lines:
        lines.append("- 근거 문장:")
        lines.extend(quote_lines)
    else:
        lines.append("- 근거 문장: 확인 불가")

    for label, names in (
        ("신호 입력", ("signal_inputs",)),
        ("진입 논리", ("entry_logic",)),
        ("청산 논리", ("exit_logic",)),
        ("필요 데이터", ("required_data",)),
    ):
        value = _first(item, *names, default=None)
        if value is not None:
            lines.append(f"- {label}: {_compact_value(value)}")
    lines.extend(
        [
            f"- 한계: {_compact_value(limitations)}",
            f"- 라이선스: {_compact_value(license_note)}",
            f"- 실행 위험 메모: {_compact_value(implementation_risk)}",
            "",
        ]
    )
    return lines


def render_briefing_markdown(payload: Any, *, settings: Any) -> str:
    """Return a bounded Korean Markdown briefing.

    ``payload`` may contain raw candidates (each with Evidence) or one or
    more results from :func:`candidate_view.build_candidate_view`.  The
    returned value is a ``str`` subclass so its ``metadata`` contains
    ``items_truncated`` and the rendered counts without changing the string
    API.
    """

    max_items = _int_setting(settings, "briefing_max_items", _DEFAULT_MAX_ITEMS)
    configured_quote_limit = _int_setting(settings, "quote_max_chars", _DEFAULT_MAX_QUOTE_CHARS, minimum=1)
    quote_limit = min(configured_quote_limit, gate.MAX_QUOTE_CHARS)
    section = _payload_section(payload)
    window = _get(section, "window", "collection_window", default={})

    raw_values = _item_sources(payload, section)
    view_records = _build_view_records(raw_values, quote_limit)
    grouped = _group_records(view_records)
    candidate_count = _get(section, "candidate_count", default=_MISSING)
    if candidate_count is _MISSING:
        candidate_count = _get(payload, "candidate_count", default=len(grouped))
    approved_count = _get(section, "approved_count", default=_MISSING)
    if approved_count is _MISSING:
        approved_count = _get(payload, "approved_count", default=_MISSING)
    if approved_count is _MISSING:
        approved_count = sum(
            1 for item in grouped if str(_first(item, "review_status", default="")).lower() == "approved"
        )
    try:
        candidate_count = int(candidate_count)
    except (TypeError, ValueError):
        candidate_count = len(grouped)
    try:
        approved_count = int(approved_count)
    except (TypeError, ValueError):
        approved_count = 0

    items_truncated = max(0, len(grouped) - max_items)
    rendered_items = grouped[:max_items]
    briefing_id = _get(section, "briefing_id", default=_get(payload, "briefing_id", default="확인 불가"))
    generated_at = _get(section, "generated_at", "created_at", default=_get(payload, "generated_at", "created_at", default=None))
    timezone = _get(section, "timezone", "time_zone", default=_get(payload, "timezone", "time_zone", default=_setting(settings, "TIMEZONE", "Asia/Seoul")))
    window_start = _window_value(section, window, "window_start", "actual_start")
    window_end = _window_value(section, window, "window_end", "actual_end")
    truncated = bool(_window_value(section, window, "window_truncated", "truncated", "is_truncated") or False)
    truncated_from = _window_value(section, window, "truncated_from", "requested_start", "requested_window_start")
    publication_status = _get(section, "publication_status", "status", default=_get(payload, "publication_status", "status", default="확인 불가"))
    run_status = _get(section, "run_status", default=_get(payload, "run_status", default=None))
    total_sources, successful_sources, failed_sources, not_executed_sources = _source_counts(payload, section)

    lines = [
        "# 스캘핑 전략 리서치 브리핑",
        "",
        f"- 브리핑 ID (`briefing_id`): {_clean_line(briefing_id)}",
        f"- 생성 시각 (`generated_at`): {_format_time(generated_at)}",
        f"- 시간대: {_clean_line(timezone)}",
        f"- 데이터 기준 구간 (`window_start`/`window_end`): {_format_time(window_start)} ~ {_format_time(window_end)}",
    ]
    if truncated:
        truncation = f" · 요청 시작 시각: {_format_time(truncated_from)}" if truncated_from else ""
        lines.append(f"- 구간 절단됨: 실제 사용 시작 시각은 {_format_time(window_start)}입니다{truncation}.")
    else:
        lines.append("- 구간 절단 없음: 기록된 window_start/window_end를 사용했습니다.")
    lines.extend(
        [
            f"- 발행 상태 (`publication_status`): {_clean_line(publication_status)}" + (f" · 실행 상태 (`run_status`): {_clean_line(run_status)}" if run_status else ""),
            f"- 수집 출처 (`source_count`): 총 {total_sources}곳 · 성공 {successful_sources} · 실패 {failed_sources} · 미실행 {not_executed_sources}",
            f"- 후보 수 (`candidate_count`): {candidate_count}개 · 승인 수 (`approved_count`): {approved_count}개",
        ]
    )
    if items_truncated:
        lines.append(f"- 항목 상한: {max_items}개 · 잘린 항목 수: {items_truncated}개")
    else:
        lines.append(f"- 항목 상한: {max_items}개 · 잘린 항목 수: 0개")
    if failed_sources:
        lines.append("- 수집 현황 메모: 일부 출처 수집 실패")
    if candidate_count > approved_count:
        lines.append("- 검토 현황 메모: 승인 대기 후보가 있습니다")
    if candidate_count == 0:
        lines.append("- 브리핑 현황 메모: 적격 신규 자료 없음")
    lines.extend(["", "## 항목"])

    if not rendered_items:
        lines.extend(
            [
                "",
                "적격 신규 자료 없음 또는 승인 대기 상태입니다.",
                "수집 실패 출처가 있으면 위 수집 현황을 확인하고, 승인 대기 후보는 검토 큐에 남깁니다.",
            ]
        )
    else:
        for index, item in enumerate(rendered_items, 1):
            lines.append("")
            lines.extend(_render_item(item, index, quote_limit))

    lines.extend(
        [
            "## 고지",
            "",
            "이 브리핑은 투자 자문·추천이 아니며, 실제 적용 전 원문·라이선스·데이터 품질과 실행 위험을 별도로 확인하고 검증해야 합니다.",
            "원문 전문을 재배포하지 않으며, 위 인용은 Evidence 추적을 위한 제한된 근거 문장입니다.",
        ]
    )
    markdown = "\n".join(lines).rstrip() + "\n"
    lint_text(markdown)
    metadata = {
        "briefing_id": briefing_id,
        "items_truncated": items_truncated,
        "rendered_item_count": len(rendered_items),
        "candidate_count": candidate_count,
        "approved_count": approved_count,
        "briefing_max_items": max_items,
        "quote_max_chars": quote_limit,
        "window_truncated": truncated,
    }
    return BriefingMarkdown(markdown, metadata)


__all__ = ["BriefingMarkdown", "render_briefing_markdown"]
