from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scalping_briefing.ops.metrics import MetricResult, ObservationWindow
from scalping_briefing.ops.report import (
    archive_report,
    render_report,
)
from scalping_briefing.publishing.phrase_lint import assert_no_banned_phrases


START = datetime(2026, 8, 3, tzinfo=UTC)
END = datetime(2026, 8, 10, tzinfo=UTC)
WINDOW = ObservationWindow(start=START, end=END, timezone="UTC")


def _metrics() -> list[MetricResult]:
    return [
        MetricResult("M1", "수집 성공률", 0.5, 0.95, "breached", 1, 2, 2),
        MetricResult("M2", "초안 지연", 10, 30, "meets_target", 10, 1, 1),
        MetricResult("M3", "검토 대기", 2, 20, "meets_target", 2, 1, 2),
        MetricResult("M4", "전달 실패율", 0.0, 0.02, "meets_target", 0, 2, 2),
        MetricResult("M5", "문서 중복률", 0.0, 0.0, "meets_target", 0, 2, 2),
        MetricResult("M6", "Evidence 누락률", None, 0.0, "insufficient_data", 0, 0, 0),
    ]


def test_report_renders_metadata_metrics_lists_and_safe_slots() -> None:
    rendered = render_report(
        WINDOW,
        _metrics(),
        report_id="ops-report-test-1",
        generated_at=datetime(2026, 8, 10, 9, tzinfo=UTC),
        settings={"LLM_MODE": "fixture", "DELIVERY_MODE": "dry_run"},
    )

    assert rendered.report_id == "ops-report-test-1"
    assert "ops-report-test-1" in rendered
    assert "2026-08-10T09:00:00+00:00" in rendered
    assert "UTC" in rendered
    assert "2026-08-03T00:00:00+00:00" in rendered
    assert "2026-08-10T00:00:00+00:00" in rendered
    assert "LLM_MODE: fixture" in rendered
    assert "DELIVERY_MODE: dry_run" in rendered
    for metric in _metrics():
        for value in (metric.metric_id, metric.value, metric.target, metric.verdict, metric.numerator, metric.denominator, metric.sample_size):
            assert (str(value) if value is not None else "—") in rendered
    assert "목표 위반 목록" in rendered and "M1" in rendered
    assert "insufficient_data" in rendered and "M6" in rendered
    assert "4주 연속 관찰 상태" in rendered
    assert "상태: insufficient_data" in rendered
    assert "확장 권고" in rendered and "hold" in rendered
    assert "## 고지" in rendered
    assert_no_banned_phrases(rendered)
    assert "TELEGRAM_BOT_TOKEN" not in rendered
    assert "chat_id" not in rendered
    assert "원문 전문" not in rendered


def test_report_archive_uses_requested_output_directory_and_same_body(tmp_path: Path) -> None:
    rendered = render_report(WINDOW, _metrics(), report_id="archive-check")

    archived = archive_report(rendered, output_dir=tmp_path)

    assert archived == tmp_path / "archive-check.md"
    assert archived.is_file()
    assert archived.read_text(encoding="utf-8") == rendered
