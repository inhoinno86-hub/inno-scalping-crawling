"""Orchestrate gated, idempotent briefing delivery.

Dry-run by default; live only when the connector is a live provider and
settings explicitly opt in via ``DELIVERY_MODE=live`` (see
``_delivery_dry_run``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
import hashlib

from scalping_briefing.delivery import guard
from scalping_briefing.models import Briefing, Delivery
from scalping_briefing.models.base import utc_now
from scalping_briefing.publishing import briefing_gate


_MISSING = object()
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


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _delivery_dry_run(settings: Any) -> bool:
    """``False`` only when settings explicitly opt into ``DELIVERY_MODE=live``.

    A live-capable connector still decides for itself whether to perform a
    real send (see ``TelegramLiveConnector``); this only carries the signal
    down instead of hardcoding it, so a live connector actually has one to
    read.
    """

    mode = str(_setting(settings, "DELIVERY_MODE", "dry_run") or "dry_run")
    return mode.strip().lower() != "live"


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


def _source_link(value: Any) -> str | None:
    for name in (
        "source_url",
        "source_link",
        "original_url",
        "canonical_url",
    ):
        link = _field(value, name, None)
        if isinstance(link, str) and link.strip():
            return link.strip()

    version = _field(value, "document_version", None)
    document = _field(version, "document", None)
    for owner in (version, document):
        for name in ("source_url", "original_url", "canonical_url"):
            link = _field(owner, name, None)
            if isinstance(link, str) and link.strip():
                return link.strip()
    return None


def _evidence_payload(evidence: Any) -> dict[str, Any]:
    if isinstance(evidence, Mapping):
        result = {
            key: value
            for key, value in evidence.items()
            if key not in {"document_version", "strategy_candidate"}
        }
    else:
        result = {}
        for name in _EVIDENCE_FIELDS:
            value = _field(evidence, name, _MISSING)
            if value is not _MISSING and value is not None:
                result["metadata" if name == "metadata_json" else name] = value

    if "document_version_id" not in result:
        version = _field(evidence, "document_version", None)
        version_id = _field(version, "document_version_id", None)
        if version_id is not None:
            result["document_version_id"] = version_id

    link = _source_link(evidence)
    if link:
        result.setdefault("source_url", link)
        result.setdefault("source_link", link)
    return result


def _candidate_payload(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        result = {
            key: value
            for key, value in candidate.items()
            if key not in {"evidence", "strategy_candidate"}
        }
    else:
        result = {}
        for name in _CANDIDATE_FIELDS:
            value = _field(candidate, name, _MISSING)
            if value is not _MISSING and value is not None:
                result["metadata" if name == "metadata_json" else name] = value
    return result


def _item_payload(item: Any, briefing_id: str) -> dict[str, Any]:
    candidate = _field(item, "strategy_candidate", None)
    if candidate is None and isinstance(item, Mapping):
        candidate = item.get("candidate")

    result = _candidate_payload(candidate) if candidate is not None else {}
    if isinstance(item, Mapping):
        result.update(
            {
                key: value
                for key, value in item.items()
                if key not in {"candidate", "strategy_candidate", "evidence"}
            }
        )
    else:
        for name in (
            "briefing_item_id",
            "briefing_id",
            "strategy_candidate_id",
            "strategy_id",
            "reason_included",
            "rank",
            "carried_over",
            "core_claim",
        ):
            value = _field(item, name, _MISSING)
            if value is not _MISSING and value is not None:
                result[name] = value

    result.setdefault("briefing_id", briefing_id)
    if "strategy_candidate_id" not in result:
        candidate_id = _field(candidate, "candidate_id", None)
        if candidate_id is not None:
            result["strategy_candidate_id"] = candidate_id
    if "candidate_id" not in result and "strategy_candidate_id" in result:
        result["candidate_id"] = result["strategy_candidate_id"]

    item_evidence = _field(item, "evidence", _MISSING)
    if item_evidence is _MISSING and candidate is not None:
        item_evidence = _field(candidate, "evidence", [])
    evidence = [_evidence_payload(row) for row in _records(item_evidence)]
    result["evidence"] = evidence
    for row in evidence:
        link = row.get("source_url")
        if isinstance(link, str) and link.strip():
            result.setdefault("source_url", link)
            result.setdefault("source_link", link)
            break
    return result


def _briefing_payload(briefing: Any, settings: Any) -> dict[str, Any]:
    if isinstance(briefing, Mapping):
        payload = {
            key: value
            for key, value in briefing.items()
            if key not in {"deliveries", "delivery_history"}
        }
        briefing_id = str(payload.get("briefing_id", ""))
        payload["items"] = [
            _item_payload(item, briefing_id)
            for item in _records(payload.get("items", []))
        ]
    else:
        names = (
            "briefing_id",
            "scheduled_for",
            "trigger_type",
            "run_attempt",
            "window_start",
            "window_end",
            "window_truncated",
            "truncated_from",
            "run_status",
            "publication_status",
            "generated_at",
            "timezone",
            "source_summary",
            "candidate_count",
            "approved_count",
            "items_truncated",
        )
        payload = {
            name: value
            for name in names
            if (value := _field(briefing, name, _MISSING)) is not _MISSING
        }
        briefing_id = str(payload.get("briefing_id", ""))
        payload["items"] = [
            _item_payload(item, briefing_id)
            for item in _records(_field(briefing, "items", []))
        ]

    # Keep the setting visible to the existing renderer/gate without adding a
    # configuration key or mutating the persisted briefing.
    payload.setdefault(
        "publication_policy",
        _setting(settings, "publication_policy", "manual_approval"),
    )
    return payload


def _briefing_record(session: Any, briefing: Any) -> Any:
    if isinstance(briefing, str):
        record = session.get(Briefing, briefing)
        if record is None:
            raise LookupError(f"briefing not found: {briefing}")
        return record
    return briefing


def _history(briefing: Any) -> list[Any]:
    return list(
        _records(
            _field(
                briefing,
                "deliveries",
                _field(briefing, "delivery_history", []),
            )
        )
    )


def _result_value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _channel(connector: Any, settings: Any) -> str:
    value = _field(connector, "channel", _setting(settings, "DELIVERY_CHANNEL", "telegram"))
    if not isinstance(value, str) or not value.strip():
        raise ValueError("delivery channel must be a non-empty string")
    return value.strip()


def _attempt_number(
    history: list[Any],
    idempotency_key: str,
    *,
    resend_reason: str | None,
    resend_approved_by: str | None,
) -> int:
    matching = [
        record
        for record in history
        if _field(record, "idempotency_key", None) == idempotency_key
    ]
    if not matching:
        return 1

    # Call both public policy predicates at this boundary.  If approval is
    # incomplete, next_attempt_no is deliberately called again so its exact
    # existing guard exception reaches the caller unchanged.
    approved = guard.resend_is_approved(resend_reason, resend_approved_by)
    allowed = guard.can_resend(
        matching,
        resend_reason=resend_reason,
        resend_approved_by=resend_approved_by,
    )
    if not allowed or not approved:
        return guard.next_attempt_no(
            matching,
            resend_reason=resend_reason,
            resend_approved_by=resend_approved_by,
        )
    return guard.next_attempt_no(
        matching,
        resend_reason=resend_reason,
        resend_approved_by=resend_approved_by,
    )


def _existing_delivery(history: list[Any], idempotency_key: str) -> Delivery | None:
    rows = [
        record
        for record in history
        if isinstance(record, Delivery)
        and _field(record, "idempotency_key", None) == idempotency_key
    ]
    if not rows:
        return None
    return max(rows, key=lambda row: int(_field(row, "attempt_no", 1) or 1))


def deliver_briefing(
    session: Any,
    briefing: Any,
    *,
    connector: Any,
    settings: Any,
    resend_reason: str | None = None,
    resend_approved_by: str | None = None,
) -> Delivery | None:
    """Gate, render, guard, dry-run send, and persist one Delivery attempt."""

    briefing_record = _briefing_record(session, briefing)
    history = _history(briefing_record)
    payload = _briefing_payload(briefing_record, settings)
    if resend_reason is not None:
        payload["resend_reason"] = resend_reason
    if resend_approved_by is not None:
        payload["resend_approved_by"] = resend_approved_by

    # This is intentionally the first delivery-boundary call.  Its exceptions
    # must escape before a connector can render or send anything.
    briefing_gate.gate_briefing(
        payload,
        settings=settings,
        delivery_history=history,
    )

    # An empty, manual-approval briefing is a valid report but has no delivery
    # target.  No connector call is made in that case.
    if not payload.get("items"):
        return None

    message = connector.render(payload)
    if not isinstance(message, str):
        raise TypeError("connector.render must return a string")
    content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
    briefing_id = str(_field(briefing_record, "briefing_id", payload.get("briefing_id", "")))
    channel = _channel(connector, settings)
    idempotency_key = guard.make_idempotency_key(
        briefing_id,
        channel,
        content_hash,
    )
    guard.validate_idempotency_key(idempotency_key)
    attempt_no = _attempt_number(
        history,
        idempotency_key,
        resend_reason=resend_reason,
        resend_approved_by=resend_approved_by,
    )

    result = connector.send(message, dry_run=_delivery_dry_run(settings))
    attempted_at = _result_value(result, "attempted_at", None) or utc_now()
    delivery = _existing_delivery(history, idempotency_key)
    if delivery is None:
        delivery = Delivery.for_briefing(
            briefing_id=briefing_id,
            channel=channel,
            content_hash=content_hash,
            delivery_id=_result_value(result, "delivery_id", None),
            attempt_no=attempt_no,
            resend_reason=resend_reason,
            resend_approved_by=resend_approved_by,
            status=str(_result_value(result, "status", "success")),
            attempted_at=attempted_at,
        )
        if isinstance(briefing_record, Briefing):
            delivery.briefing = briefing_record
        session.add(delivery)
    else:
        # ``idempotency_key`` is unique in the existing schema.  An approved
        # resend therefore updates that durable key's latest attempt instead
        # of manufacturing a second row that the database must reject.
        delivery.attempt_no = attempt_no
        delivery.resend_reason = resend_reason
        delivery.resend_approved_by = resend_approved_by
        delivery.attempted_at = attempted_at
        delivery.status = str(_result_value(result, "status", "success"))
        delivery.content_hash = content_hash
    session.flush()
    delivery.provider_reference = _result_value(result, "provider_reference", None)
    delivery.error = _result_value(result, "error", None)
    session.flush()
    return delivery


__all__ = ["deliver_briefing"]
