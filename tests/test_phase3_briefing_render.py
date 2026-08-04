from __future__ import annotations

import pytest

from scalping_briefing.publishing.briefing_render import render_briefing_markdown
from scalping_briefing.publishing.gate import EvidenceQuoteError, PublicationPhraseError


CORE_FIELDS = (
    "core_hypothesis",
    "signal_inputs",
    "entry_logic",
    "exit_logic",
    "required_data",
    "risk_notes",
)


def _candidate(index: int) -> dict[str, object]:
    candidate_id = f"candidate-{index}"
    candidate = {
        "candidate_id": candidate_id,
        "strategy_id": f"strategy-{index}",
        "canonical_name": f"Queue Momentum {index}",
        "summary": "Queue imbalance can precede short-horizon movement.",
        "asset_classes": ["crypto"],
        "strategy_families": ["order_flow"],
        "holding_horizon": "seconds to 30 minutes",
        "value_score": 80 + index,
        "value_score_breakdown": {"reproducibility": 22},
        "review_status": "approved",
        "source_url": f"https://example.invalid/doc/{index}",
        "document_version_ids": [f"dv-{index}"],
        "license": "CC BY",
        "core_hypothesis": "Queue imbalance can precede short-horizon movement.",
        "signal_inputs": ["Queue imbalance", "order book"],
        "entry_logic": "Enter after the documented queue imbalance condition.",
        "exit_logic": "Exit on reversal or the documented holding timeout.",
        "required_data": ["L2 quotes", "trades"],
        "risk_notes": "Latency and adverse selection require review.",
    }
    evidence = [
        {
            "evidence_id": f"e-{index}-{field}",
            "document_version_id": f"dv-{index}",
            "field_name": field,
            "quote": str(candidate[field]) if isinstance(candidate[field], str) else ", ".join(candidate[field]),
            "section_or_locator": f"{field} section",
            "source_url": candidate["source_url"],
        }
        for field in CORE_FIELDS
    ]
    return {"candidate": candidate, "evidence": evidence}


def _payload(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "briefing_id": "briefing-render-1",
        "generated_at": "2026-08-03T08:00:00+09:00",
        "timezone": "Asia/Seoul",
        "window_start": "2026-07-20T08:00:00+09:00",
        "window_end": "2026-08-03T08:00:00+09:00",
        "window_truncated": True,
        "truncated_from": "2026-07-01T08:00:00+09:00",
        "publication_status": "draft",
        "source_summary": {"total": 3, "success": 2, "failed": 1, "not_executed": 0},
        "candidate_count": len(items),
        "approved_count": len(items),
        "items": items,
    }


def test_renderer_contains_traceability_and_truncation_metadata() -> None:
    rendered = render_briefing_markdown(
        _payload([_candidate(1)]),
        settings={"briefing_max_items": 7, "quote_max_chars": 300},
    )

    assert isinstance(rendered, str)
    assert rendered.metadata["items_truncated"] == 0
    assert "briefing-render-1" in rendered
    assert "구간 절단됨" in rendered
    assert "성공 2 · 실패 1 · 미실행 0" in rendered
    assert "document_version_id" in rendered
    assert "dv-1" in rendered
    assert rendered.count("> ") == 2


def test_renderer_applies_item_limit_and_reports_cut_count() -> None:
    rendered = render_briefing_markdown(
        _payload([_candidate(index) for index in range(8)]),
        settings={"briefing_max_items": 7, "quote_max_chars": 300},
    )

    assert rendered.metadata["items_truncated"] == 1
    assert "잘린 항목 수: 1개" in rendered
    assert rendered.count("### ") == 7


def test_renderer_rejects_long_quotes_and_banned_phrases() -> None:
    long_item = _candidate(1)
    long_item["evidence"][0]["quote"] = "x" * 301  # type: ignore[index]
    with pytest.raises(EvidenceQuoteError):
        render_briefing_markdown(_payload([long_item]), settings={"quote_max_chars": 300})

    banned_item = _candidate(1)
    banned_item["candidate"]["summary"] = "투자 추천"  # type: ignore[index]
    with pytest.raises(PublicationPhraseError):
        render_briefing_markdown(_payload([banned_item]), settings={"quote_max_chars": 300})
