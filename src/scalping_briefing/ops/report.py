"""Korean Markdown reports for one operational observation window.

The report boundary accepts already-calculated metric results and produces a
small, local artifact.  It does not publish a briefing or communicate with a
provider.  Only bounded metric fields are rendered; metric detail payloads and
source content are intentionally excluded from the report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

from ..logging_setup import is_secret_key, mask_secrets
from ..publishing.phrase_lint import assert_no_banned_phrases


DEFAULT_OUTPUT_DIR = Path("storage/ops-reports/")
DEFAULT_REPORT_DIRECTORY = DEFAULT_OUTPUT_DIR
REPORT_OUTPUT_DIR = DEFAULT_OUTPUT_DIR

_DEFAULT_TIMEZONE = "Asia/Seoul"
_VERDICTS = frozenset({"meets_target", "breached", "insufficient_data"})
_SAFE_RECOMMENDATIONS = frozenset({"recommend", "hold"})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

_METRIC_SPECS: tuple[tuple[str, str, float | int], ...] = (
    ("M1", "Active-source collection success rate", 0.95),
    ("M2", "Briefing execution-to-draft delay", 30),
    ("M3", "Strategy-candidate review backlog", 20),
    ("M4", "Final delivery failure rate", 0.02),
    ("M5", "Duplicate document-version rate", 0.0),
    ("M6", "Core-claim Evidence gap rate", 0.0),
)
_METRIC_IDS = tuple(spec[0] for spec in _METRIC_SPECS)


class OperationalReportMarkdown(str):
    """String-compatible rendered report with archive metadata."""

    def __new__(
        cls,
        value: str,
        metadata: Mapping[str, Any],
    ) -> "OperationalReportMarkdown":
        rendered = super().__new__(cls, value)
        rendered.metadata = dict(metadata)
        rendered.meta = rendered.metadata
        return rendered

    @property
    def body(self) -> str:
        return str(self)

    @property
    def markdown(self) -> str:
        return str(self)

    @property
    def report_id(self) -> str:
        return str(self.metadata["report_id"])


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read one of several names from a mapping or record-like object."""

    for name in names:
        if isinstance(value, Mapping):
            if name in value:
                return value[name]
            continue
        try:
            found = getattr(value, name)
        except (AttributeError, KeyError):
            continue
        if found is not None:
            return found
    return default


def _clean_text(value: Any, *, default: str = "확인 불가", limit: int = 160) -> str:
    if value is None:
        return default
    text = str(value).replace("\x00", " ")
    text = " ".join(text.split())
    if not text:
        return default
    return text[:limit]


def _setting(settings: Any, name: str) -> Any:
    if settings is None:
        return None
    if isinstance(settings, Mapping):
        try:
            return settings.get(name)
        except (AttributeError, KeyError):
            return None
    try:
        return getattr(settings, name)
    except AttributeError:
        return None


def _mode(
    name: str,
    allowed: frozenset[str],
    explicit: str | None,
    settings: Any,
    default: str,
) -> str:
    value = explicit
    if value is None:
        value = _setting(settings, name)
    if value is None:
        value = os.environ.get(name)
    normalized = str(value).strip().lower() if value is not None else default
    return normalized if normalized in allowed else "unknown"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo(_DEFAULT_TIMEZONE)


def _format_datetime(value: Any, timezone: str) -> str:
    if value is None:
        return "확인 불가"
    if not isinstance(value, datetime):
        return _clean_text(value)
    zone = _zone(timezone)
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    else:
        value = value.astimezone(zone)
    return value.isoformat()


def _window_parts(
    window: Any,
    timezone: str | None,
    fallback_timezone: str | None = None,
) -> tuple[Any, Any, str, str]:
    start = _field(window, "start", "window_start", "actual_start")
    end = _field(window, "end", "window_end", "actual_end")
    window_timezone = _field(window, "timezone", "time_zone")
    selected_timezone = str(
        timezone
        or window_timezone
        or fallback_timezone
        or _DEFAULT_TIMEZONE
    ).strip() or _DEFAULT_TIMEZONE
    window_id = _field(window, "window_id", "id")
    if window_id is None:
        canonical = "|".join(
            (_format_datetime(start, selected_timezone), _format_datetime(end, selected_timezone), selected_timezone)
        )
        window_id = sha256(canonical.encode("utf-8")).hexdigest()
    return start, end, selected_timezone, _clean_text(window_id, limit=255)


def _safe_report_id(value: Any, *, window_id: str) -> str:
    candidate = _clean_text(value, default="", limit=120) if value is not None else ""
    if not candidate:
        candidate = f"ops-report-{window_id}"
    if _SAFE_IDENTIFIER.fullmatch(candidate):
        return candidate
    digest = sha256(candidate.encode("utf-8")).hexdigest()
    return f"ops-report-{digest}"


def _metric_records(metrics: Any) -> dict[str, Any]:
    if metrics is None:
        return {}
    if isinstance(metrics, Mapping):
        if _field(metrics, "metric_id", "id") is not None:
            values: list[tuple[Any, Any]] = [(None, metrics)]
        else:
            values = list(metrics.items())
    elif isinstance(metrics, (str, bytes, bytearray)):
        values = []
    else:
        try:
            values = [(None, value) for value in metrics]
        except TypeError:
            values = [(None, metrics)]

    records: dict[str, Any] = {}
    for supplied_id, value in values:
        metric_id = _field(value, "metric_id", "id")
        if metric_id is None:
            metric_id = supplied_id
        if metric_id is None:
            continue
        normalized = str(metric_id).strip().upper()
        if normalized in _METRIC_IDS:
            records[normalized] = value
    return records


def _number(value: Any, default: float | int | None = None) -> float | int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    return default


def _metric_rows(metrics: Any) -> list[dict[str, Any]]:
    records = _metric_records(metrics)
    rows: list[dict[str, Any]] = []
    for metric_id, default_title, default_target in _METRIC_SPECS:
        record = records.get(metric_id)
        title = _clean_text(
            _field(record, "title", "name", default=default_title),
            default=default_title,
            limit=120,
        )
        target = _number(
            _field(record, "target", default=default_target),
            default=default_target,
        )
        sample_size = _number(
            _field(record, "sample_size", "sample", default=0),
            default=0,
        )
        try:
            sample_size = max(0, int(sample_size or 0))
        except (TypeError, ValueError):
            sample_size = 0
        verdict = str(
            _field(record, "verdict", default="insufficient_data")
        ).strip().lower()
        if verdict not in _VERDICTS:
            verdict = "insufficient_data" if sample_size == 0 else "unknown"
        rows.append(
            {
                "metric_id": metric_id,
                "title": title,
                "value": _number(_field(record, "value")),
                "target": target,
                "verdict": verdict,
                "numerator": _number(_field(record, "numerator")),
                "denominator": _number(_field(record, "denominator")),
                "sample_size": sample_size,
            }
        )
    return rows


def _format_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return str(value)
    return _clean_text(value, limit=80)


def _status(value: Any) -> tuple[str, str, str]:
    if isinstance(value, Mapping):
        eligible = _field(value, "expansion_eligible", "eligible")
        raw_status = _field(value, "status", "verdict")
        raw_reason = _field(value, "reason", "blocking_reason")
        if raw_status is None and isinstance(eligible, bool):
            raw_status = "meets_target" if eligible else "insufficient_data"
        status = str(raw_status or "insufficient_data").strip().lower()
        reason = str(raw_reason or status).strip().lower()
        eligibility = "예" if eligible is True else "아니오" if eligible is False else "확인 불가"
        return (
            status if status in _VERDICTS else "insufficient_data",
            reason if reason in _VERDICTS else "insufficient_data",
            eligibility,
        )
    if isinstance(value, bool):
        return ("meets_target" if value else "insufficient_data", "insufficient_data", "예" if value else "아니오")
    candidate = str(value).strip().lower() if value is not None else "insufficient_data"
    if candidate in _VERDICTS:
        return candidate, candidate, "확인 불가"
    return "insufficient_data", "insufficient_data", "확인 불가"


def _recommendation(value: Any) -> tuple[str, str]:
    if value is None:
        return "hold", "측정 데이터 부족 — 확장하지 않음"
    if isinstance(value, Mapping):
        raw = _field(value, "recommendation", "decision", "action", default="hold")
        reason = _field(value, "reason", "status", default="측정 데이터 부족 — 확장하지 않음")
    else:
        raw = value
        reason = "측정 데이터 부족 — 확장하지 않음"
    recommendation = str(raw).strip().lower()
    if recommendation not in _SAFE_RECOMMENDATIONS:
        recommendation = _clean_text(raw, default="hold", limit=80)
    return recommendation, _clean_text(reason, default="측정 데이터 부족 — 확장하지 않음", limit=160)


def _secret_values(settings: Any) -> set[str]:
    values: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if is_secret_key(key) and isinstance(nested, str) and nested:
                    values.add(nested)
                elif isinstance(nested, (Mapping, list, tuple)):
                    collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(settings)
    collect(os.environ)
    return values


def _safe_body(body: str, *, settings: Any = None) -> str:
    masked = mask_secrets(body, secret_values=_secret_values(settings))
    return assert_no_banned_phrases(str(masked))


def _looks_like_window(value: Any) -> bool:
    return _field(value, "start", "window_start") is not None and _field(value, "end", "window_end") is not None


def render_operational_report(
    window: Any = None,
    metrics: Any = None,
    *,
    report_id: str | None = None,
    generated_at: datetime | None = None,
    timezone: str | None = None,
    settings: Any = None,
    llm_mode: str | None = None,
    delivery_mode: str | None = None,
    four_week_status: Any = None,
    expansion_recommendation: Any = None,
    session: Any = None,
    observation_window: Any = None,
    metric_results: Any = None,
    results: Any = None,
    four_week: Any = None,
    expansion: Any = None,
) -> OperationalReportMarkdown:
    """Render a Korean report from one window and its six metric results.

    A session may be supplied for convenience; in that case the existing
    read-only metric aggregator is used.  The normal report path accepts
    precomputed results and therefore has no database or delivery side effect.
    """

    # Support the convenient ``render_report(session, window)`` form without
    # making the report renderer depend on a session type.
    if window is not None and not _looks_like_window(window) and _looks_like_window(metrics):
        actual_window = metrics
        if session is None and hasattr(window, "scalars"):
            session = window
            actual_metrics = None
        else:
            actual_metrics = window
        window, metrics = actual_window, actual_metrics
    if window is None:
        window = observation_window
    if metrics is None:
        metrics = metric_results if metric_results is not None else results
    if four_week_status is None:
        four_week_status = four_week
    if expansion_recommendation is None:
        expansion_recommendation = expansion
    if window is None:
        raise TypeError("window is required")

    start, end, selected_timezone, window_id = _window_parts(
        window,
        timezone,
        _setting(settings, "TIMEZONE"),
    )
    if metrics is None and session is not None:
        from .metrics import compute_all_metrics

        metrics = compute_all_metrics(session, window, settings=settings, delivery_mode=delivery_mode)
    rows = _metric_rows(metrics)
    selected_llm_mode = _mode(
        "LLM_MODE", frozenset({"fixture", "live"}), llm_mode, settings, "fixture"
    )
    selected_delivery_mode = _mode(
        "DELIVERY_MODE", frozenset({"dry_run", "live"}), delivery_mode, settings, "dry_run"
    )
    safe_report_id = _safe_report_id(report_id, window_id=window_id)
    if generated_at is None:
        generated_at = datetime.now(_zone(selected_timezone))
    four_status, four_reason, four_eligible = _status(four_week_status)
    recommendation, recommendation_reason = _recommendation(expansion_recommendation)

    lines = [
        "# 운영 지표 리포트",
        "",
        f"- 리포트 ID (`report_id`): {safe_report_id}",
        f"- 생성 시각 (`generated_at`): {_format_datetime(generated_at, selected_timezone)}",
        f"- 시간대 (`timezone`): {selected_timezone}",
        f"- 관측 창 시작 (`window_start`): {_format_datetime(start, selected_timezone)}",
        f"- 관측 창 종료 (`window_end`): {_format_datetime(end, selected_timezone)}",
        f"- LLM_MODE: {selected_llm_mode}",
        f"- DELIVERY_MODE: {selected_delivery_mode}",
        "",
        "## 지표",
        "",
        "| ID | 지표 | 값 | 목표 | 판정 | 분자 | 분모 | 표본 수 |",
        "| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {metric_id} | {title} | {value} | {target} | {verdict} | {numerator} | {denominator} | {sample_size} |".format(
                metric_id=row["metric_id"],
                title=row["title"],
                value=_format_scalar(row["value"]),
                target=_format_scalar(row["target"]),
                verdict=row["verdict"],
                numerator=_format_scalar(row["numerator"]),
                denominator=_format_scalar(row["denominator"]),
                sample_size=_format_scalar(row["sample_size"]),
            )
        )

    breached = [row["metric_id"] for row in rows if row["verdict"] == "breached"]
    insufficient = [
        row["metric_id"] for row in rows if row["verdict"] == "insufficient_data"
    ]
    lines.extend(
        [
            "",
            "## 목표 위반 목록",
            "",
            f"- {', '.join(breached) if breached else '없음'}",
            "",
            "## insufficient_data 목록",
            "",
            f"- {', '.join(insufficient) if insufficient else '없음'}",
            "",
            "## 4주 연속 관찰 상태",
            "",
            f"- 상태: {four_status}",
            f"- 사유: {four_reason}",
            f"- 확장 적격성: {four_eligible}",
            "",
            "## 확장 권고",
            "",
            f"- 종합 권고: {recommendation}",
            f"- 사유: {recommendation_reason}",
            "- 자동 발행 (`publication_policy: auto_publish`): hold",
            "- 출처 확대 (`active: true`): hold",
            "- 검색 UI: hold",
            "",
            "## 고지",
            "",
            "이 문서는 시스템 운영 측정과 점검을 위한 정보이며, 투자·거래 판단을 대신하지 않습니다.",
            "판정은 관측 창 안에서 확인된 구조화된 기록에 한정하며, 표본이 없으면 목표 충족으로 간주하지 않습니다.",
        ]
    )
    body = _safe_body("\n".join(lines).rstrip() + "\n", settings=settings)
    metadata = {
        "report_id": safe_report_id,
        "generated_at": _format_datetime(generated_at, selected_timezone),
        "timezone": selected_timezone,
        "window_id": window_id,
        "window_start": _format_datetime(start, selected_timezone),
        "window_end": _format_datetime(end, selected_timezone),
        "LLM_MODE": selected_llm_mode,
        "DELIVERY_MODE": selected_delivery_mode,
        "breached": tuple(breached),
        "insufficient_data": tuple(insufficient),
        "four_week_status": four_status,
        "expansion_recommendation": recommendation,
    }
    return OperationalReportMarkdown(body, metadata)


def archive_operational_report(
    report: Any,
    markdown: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    *,
    report_id: str | None = None,
) -> Path:
    """Archive one rendered report below ``output_dir`` and return its path."""

    # Also accept ``archive_report(rendered, tmp_path)``.
    if markdown is not None and isinstance(markdown, os.PathLike) and output_dir == DEFAULT_OUTPUT_DIR:
        output_dir = markdown
        markdown = None

    if markdown is None:
        body = _field(report, "body", "markdown")
        if body is None:
            body = str(report)
        selected_id = report_id or _field(report, "report_id")
    else:
        body = str(markdown)
        selected_id = report_id
        if selected_id is None and isinstance(report, str) and _SAFE_IDENTIFIER.fullmatch(report) and "\n" in body:
            selected_id = report

    body = _safe_body(str(body))
    if selected_id is None:
        selected_id = f"ops-report-{sha256(body.encode('utf-8')).hexdigest()}"
    safe_id = _safe_report_id(selected_id, window_id=sha256(body.encode("utf-8")).hexdigest())
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{safe_id}.md"
    target.write_text(body, encoding="utf-8")
    return target


# Public package-C names kept short for callers and the phase-4 report tests.
render_report = render_operational_report
archive_report = archive_operational_report


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_REPORT_DIRECTORY",
    "REPORT_OUTPUT_DIR",
    "OperationalReportMarkdown",
    "archive_operational_report",
    "archive_report",
    "render_operational_report",
    "render_report",
]


def write_operational_report(
    window: Any = None,
    metrics: Any = None,
    *,
    output_dir: str | os.PathLike[str] = DEFAULT_OUTPUT_DIR,
    **kwargs: Any,
) -> Path:
    """Render and archive one report without creating a domain record."""

    rendered = render_operational_report(window, metrics, **kwargs)
    return archive_operational_report(rendered, output_dir=output_dir)


# Stable short names for callers that use the report boundary directly.
render_report = render_operational_report
build_operational_report = render_operational_report
archive_report = archive_operational_report
write_report = write_operational_report
generate_report = write_operational_report
render_and_archive_report = write_operational_report


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_REPORT_DIRECTORY",
    "REPORT_OUTPUT_DIR",
    "OperationalReportMarkdown",
    "archive_operational_report",
    "archive_report",
    "build_operational_report",
    "generate_report",
    "render_and_archive_report",
    "render_operational_report",
    "render_report",
    "write_operational_report",
    "write_report",
]
