from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scalping_briefing.delivery.guard import (
    DeliveryHistory,
    ResendApprovalRequired,
)
from scalping_briefing.publishing import briefing_gate
from scalping_briefing.publishing.gate import (
    MissingEvidenceError,
    OriginalFullTextError,
    OriginalSourceLinkError,
    PublicationPhraseError,
)


ROOT = Path(__file__).resolve().parents[1]
SETTINGS = {
    "quote_max_chars": 300,
    "DELIVERY_CHANNEL": "telegram",
}


def _evidence(
    *,
    evidence_id: str = "e-1",
    source_url: str | None = "https://example.invalid/source",
) -> dict[str, object]:
    record: dict[str, object] = {
        "evidence_id": evidence_id,
        "document_version_id": "dv-1",
        "strategy_candidate_id": "candidate-1",
        "field_name": "summary",
        "quote": "A bounded source-backed observation.",
        "section_or_locator": "abstract",
    }
    if source_url is not None:
        record["source_url"] = source_url
    return record


def _item(
    *,
    candidate_id: str = "candidate-1",
    item_id: str = "item-1",
    review_status: str = "approved",
) -> dict[str, object]:
    return {
        "briefing_item_id": item_id,
        "briefing_id": "briefing-1",
        "strategy_candidate_id": candidate_id,
        "reason_included": "approved source-backed candidate",
        "rank": 1,
        "carried_over": False,
        "canonical_name": "Queue Momentum",
        "summary": "A bounded source-backed observation.",
        "review_status": review_status,
        "source_url": "https://example.invalid/source",
        "document_version_ids": ["dv-1"],
        "evidence": [_evidence()],
    }


def _payload(*items: dict[str, object]) -> dict[str, object]:
    return {
        "briefing_id": "briefing-1",
        "scheduled_for": "2026-08-03T08:00:00+09:00",
        "generated_at": "2026-08-03T08:00:00+09:00",
        "timezone": "Asia/Seoul",
        "window_start": "2026-07-20T08:00:00+09:00",
        "window_end": "2026-08-03T08:00:00+09:00",
        "window_truncated": False,
        "run_status": "success",
        "publication_status": "approved",
        "source_summary": {"total": 1, "success": 1, "failed": 0, "not_executed": 0},
        "deduplicated": True,
        "items": list(items),
    }


def test_gate_briefing_accepts_normal_payload_and_returns_same_object() -> None:
    payload = _payload(_item())

    assert briefing_gate.gate_briefing(payload, settings=SETTINGS) is payload


def test_gate_reuses_existing_publication_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original_publication = briefing_gate.gate.validate_publication
    original_item = briefing_gate.gate.validate_briefing_item

    def observed_publication(publication: object, **limits: int) -> object:
        calls.append("publication")
        return original_publication(publication, **limits)

    def observed_item(item: object, **limits: int) -> object:
        calls.append("item")
        return original_item(item, **limits)

    monkeypatch.setattr(briefing_gate.gate, "validate_publication", observed_publication)
    monkeypatch.setattr(briefing_gate.gate, "validate_briefing_item", observed_item)

    briefing_gate.gate_briefing(_payload(_item()), settings=SETTINGS)

    assert calls.count("publication") >= 2
    assert calls.count("item") >= 1


def test_gate_rejects_each_source_and_traceability_violation() -> None:
    without_source_policy = _payload(_item())
    without_source_policy["source_allowed"] = False
    with pytest.raises(briefing_gate.BriefingSourceError):
        briefing_gate.gate_briefing(without_source_policy, settings=SETTINGS)

    without_link = _payload(_item())
    without_link["items"][0]["source_url"] = None  # type: ignore[index]
    without_link["items"][0]["evidence"] = [_evidence(source_url=None)]  # type: ignore[index]
    with pytest.raises(OriginalSourceLinkError):
        briefing_gate.gate_briefing(without_link, settings=SETTINGS)

    without_evidence = _payload(_item())
    without_evidence["items"][0]["evidence"] = []  # type: ignore[index]
    with pytest.raises(MissingEvidenceError):
        briefing_gate.gate_briefing(without_evidence, settings=SETTINGS)


def test_gate_rejects_missing_version_or_data_window() -> None:
    without_version = _payload(_item())
    without_version["items"][0]["evidence"][0]["document_version_id"] = ""  # type: ignore[index]
    with pytest.raises(ValueError, match="document_version_id"):
        briefing_gate.gate_briefing(without_version, settings=SETTINGS)

    without_window = _payload(_item())
    del without_window["window_end"]
    with pytest.raises(briefing_gate.BriefingWindowError):
        briefing_gate.gate_briefing(without_window, settings=SETTINGS)


def test_gate_rejects_duplicate_items_without_relationship() -> None:
    duplicate = _payload(
        _item(item_id="item-1"),
        _item(item_id="item-2"),
    )

    with pytest.raises(briefing_gate.BriefingRelationshipError):
        briefing_gate.gate_briefing(duplicate, settings=SETTINGS)

    related = deepcopy(duplicate)
    related["items"][1]["relationship_to_existing"] = "same strategy; retained for comparison"  # type: ignore[index]
    assert briefing_gate.gate_briefing(related, settings=SETTINGS) is related


def test_gate_rejects_unapproved_item_unless_internal_draft_is_explicit() -> None:
    pending = _payload(_item(review_status="needs_review"))
    with pytest.raises(briefing_gate.BriefingApprovalError):
        briefing_gate.gate_briefing(pending, settings=SETTINGS)

    internal = deepcopy(pending)
    internal["publication_status"] = "draft"
    internal["internal_draft"] = True
    assert briefing_gate.gate_briefing(internal, settings=SETTINGS) is internal


def test_gate_reuses_phrase_full_text_and_sensitive_guards() -> None:
    banned = _payload(_item())
    banned["items"][0]["summary"] = "투자 추천"  # type: ignore[index]
    with pytest.raises(PublicationPhraseError):
        briefing_gate.gate_briefing(banned, settings=SETTINGS)

    full_text = _payload(_item())
    full_text["items"][0]["full_text"] = "original body"  # type: ignore[index]
    with pytest.raises(OriginalFullTextError):
        briefing_gate.gate_briefing(full_text, settings=SETTINGS)

    sensitive = _payload(_item())
    sensitive["sensitive_data"] = "operator token"  # type: ignore[index]
    with pytest.raises(briefing_gate.SensitiveInformationError):
        briefing_gate.gate_briefing(sensitive, settings=SETTINGS)


def test_gate_delegates_duplicate_delivery_history_to_delivery_guard() -> None:
    history = DeliveryHistory(status="success", attempt_no=1)
    with pytest.raises(ResendApprovalRequired):
        briefing_gate.gate_briefing(
            _payload(_item()),
            settings=SETTINGS,
            delivery_history=history,
        )

    resend = _payload(_item())
    resend["resend_reason"] = "operator-reviewed"
    resend["resend_approved_by"] = "reviewer-1"
    assert (
        briefing_gate.gate_briefing(
            resend,
            settings=SETTINGS,
            delivery_history=history,
        )
        is resend
    )


def test_no_gate_bypass_render_or_delivery_path_exists() -> None:
    source = (ROOT / "src/scalping_briefing/publishing/briefing_gate.py").read_text(
        encoding="utf-8"
    )
    assert "render_briefing_markdown" not in source
    assert "delivery.service" not in source
    assert "send(" not in source

