"""Briefing-level publication gate.

The existing :mod:`publishing.gate` owns the renderer-facing publication
contract.  This module only composes that contract with the metadata that is
available at briefing level and with the existing delivery resend guard.  It
does not render, persist, or send a briefing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
import re
from typing import Any, Final

from scalping_briefing.delivery import guard as delivery_guard

from . import gate


_MISSING: Final[object] = object()


class BriefingGateError(ValueError):
    """Base error for a briefing that cannot enter a delivery path."""


class BriefingSourceError(BriefingGateError):
    """A briefing contains an explicitly disallowed source."""


class BriefingWindowError(BriefingGateError):
    """A briefing does not record a valid data window."""


class BriefingRelationshipError(BriefingGateError):
    """Duplicate items are present without a relationship or deduplication note."""


class BriefingApprovalError(BriefingGateError):
    """A non-approved item is not explicitly marked as an internal draft."""


class SensitiveInformationError(BriefingGateError):
    """Sensitive information was supplied to a publication payload."""


# Compatibility aliases keep callers from depending on the condition names.
PublicationBriefingGateError = BriefingGateError
BriefingPublicationError = BriefingGateError
InvalidBriefingError = BriefingGateError
SourcePolicyError = BriefingSourceError
MissingDataWindowError = BriefingWindowError
DuplicateBriefingItemError = BriefingRelationshipError
UnapprovedBriefingError = BriefingApprovalError


_CORE_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "corehypothesis",
        "signalinputs",
        "entrylogic",
        "exitlogic",
        "requireddata",
        "risknotes",
    }
)
_SOURCE_CONTAINER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source",
        "sources",
        "sourceresult",
        "sourceresults",
        "sourcestatus",
        "sourcestatuses",
        "sourcepolicy",
        "accesspolicy",
        "document",
        "documentversion",
    }
)
_SOURCE_BOOLEAN_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sourceallowed",
        "allowedsource",
        "allowlisted",
        "policyallowed",
        "sourcepolicyallowed",
        "accessallowed",
        "sourceapproved",
        "sourceactive",
        "sourceenabled",
        "sourcepolicyvalid",
        "robotsallowed",
    }
)
_SOURCE_STATUS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sourcestatus",
        "accessstatus",
        "accessdecision",
        "policydecision",
        "sourcedecision",
    }
)
_SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sensitive",
        "sensitiveinfo",
        "sensitivedata",
        "sensitiveinformation",
        "containsensitive",
        "containsensitivedata",
        "containsensitiveinformation",
        "hassensitivedata",
        "pii",
        "personalinfo",
        "personalinformation",
        "secret",
        "secrets",
        "token",
        "apikey",
        "accesstoken",
        "authorization",
        "bot token".replace(" ", ""),
        "chatid",
        "password",
        "privatekey",
        "credential",
        "credentials",
    }
)
_RELATION_KEYS: Final[tuple[str, ...]] = (
    "relationship_to_existing",
    "existing_strategy_relationship",
    "existing_strategy_relation",
    "relationship",
    "relation",
    "related_strategy_ids",
    "related_strategies",
    "duplicate_of",
    "deduplication",
    "deduplication_note",
    "deduplication_notes",
    "novelty_status",
)
_INTERNAL_MARKER_KEYS: Final[frozenset[str]] = frozenset(
    {
        "internaldraft",
        "isinternaldraft",
        "internalonly",
        "draftonly",
        "notforexternal",
        "internal",
    }
)


def _key_name(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _field(record: object, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _first(record: object, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        value = _field(record, name, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    return default


def _pairs(record: object) -> Iterable[tuple[str, object]]:
    if isinstance(record, Mapping):
        yield from ((str(key), value) for key, value in record.items())
        return
    if isinstance(record, (str, bytes, bytearray)):
        return
    if isinstance(record, Sequence):
        yield from (("", value) for value in record)
        return
    values = getattr(record, "__dict__", None)
    if isinstance(values, Mapping):
        yield from (
            (str(key), value)
            for key, value in values.items()
            if not str(key).startswith("_")
        )


def _records(value: object) -> list[object]:
    if value is None or value is _MISSING:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes, bytearray)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _section(payload: object) -> object:
    nested = _field(payload, "briefing", _MISSING)
    if nested is not _MISSING and nested is not None:
        return nested
    return payload


def _items(payload: object) -> list[object]:
    section = _section(payload)
    value = _first(section, "items", "briefing_items", default=_MISSING)
    if value is _MISSING:
        value = _first(payload, "items", "briefing_items", default=_MISSING)
    if value is not _MISSING:
        return _records(value)
    evidence = _first(section, "evidence", default=_MISSING)
    return [section] if evidence is not _MISSING else []


def _setting(settings: object, name: str, default: Any = None) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    try:
        value = getattr(settings, name)
    except (AttributeError, KeyError):
        return default
    return default if value is None else value


def _filled(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value is not _MISSING


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _data_window(section: object) -> tuple[object, object]:
    nested = _first(
        section,
        "window",
        "collection_window",
        "data_window",
        default={},
    )
    start = _first(section, "window_start", "actual_start", default=_MISSING)
    end = _first(section, "window_end", "actual_end", default=_MISSING)
    if start is _MISSING:
        start = _first(
            nested,
            "window_start",
            "actual_start",
            "requested_start",
            "start",
            default=_MISSING,
        )
    if end is _MISSING:
        end = _first(
            nested,
            "window_end",
            "actual_end",
            "requested_end",
            "end",
            default=_MISSING,
        )
    return start, end


def _require_data_window(section: object) -> None:
    start, end = _data_window(section)
    if not _filled(start) or not _filled(end):
        raise BriefingWindowError(
            "briefing must record both window_start and window_end"
        )
    parsed_start = _as_datetime(start)
    parsed_end = _as_datetime(end)
    if parsed_start is None or parsed_end is None:
        raise BriefingWindowError(
            "window_start and window_end must be date-time values"
        )
    if parsed_start > parsed_end:
        raise BriefingWindowError("window_start must not be after window_end")


def _is_false(value: object) -> bool:
    if value is False:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {
            "false",
            "no",
            "denied",
            "deny",
            "blocked",
            "inactive",
            "disabled",
            "unknown",
            "not_allowed",
            "not-allowed",
        }
    return False


def _is_bad_status(value: object) -> bool:
    if _is_false(value):
        return True
    return isinstance(value, str) and value.strip().lower() in {
        "failed",
        "rejected",
        "denied",
        "blocked",
        "inactive",
        "unknown",
        "not_allowed",
        "not-allowed",
    }


def _assert_sources_allowed(payload: object, items: Sequence[object]) -> None:
    """Reject explicit source-policy denials before delivery.

    A source without a policy marker is handled by ``publishing.gate`` through
    its original-link validation.  This helper only interprets explicit
    policy/access fields; it does not duplicate URL safety logic.
    """

    roots = [_section(payload), *items]
    seen: set[int] = set()

    def walk(value: object, *, source_scope: bool = False, path: str = "") -> None:
        if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, nested in _pairs(value):
            normalized = _key_name(key)
            current_path = f"{path}.{key}" if path and key else (key or path)
            child_scope = source_scope or normalized in _SOURCE_CONTAINER_KEYS
            if normalized in _SOURCE_BOOLEAN_KEYS and _is_false(nested):
                raise BriefingSourceError(
                    f"briefing contains a disallowed source marker at {current_path}"
                )
            if normalized in {"sourcepolicy", "accesspolicy"} and _is_bad_status(nested):
                raise BriefingSourceError(
                    f"briefing contains a disallowed source policy at {current_path}"
                )
            if normalized in _SOURCE_STATUS_KEYS and _is_bad_status(nested):
                raise BriefingSourceError(
                    f"briefing contains a disallowed source status at {current_path}"
                )
            if child_scope and normalized in {"active", "enabled", "allowed"} and _is_false(nested):
                raise BriefingSourceError(
                    f"briefing contains a disallowed source marker at {current_path}"
                )
            walk(nested, source_scope=child_scope, path=current_path)

    for root in roots:
        walk(root)


def _value_present(value: object) -> bool:
    if value is None or value is _MISSING:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Sequence, Mapping, set)):
        return bool(value)
    return bool(value)


def _has_sensitive_value(value: object) -> bool:
    if value is None or value is _MISSING:
        return False
    if isinstance(value, bool):
        return value
    return _value_present(value)


def _assert_no_sensitive_information(payload: object) -> None:
    seen: set[int] = set()

    def walk(value: object, path: str = "") -> None:
        if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        for key, nested in _pairs(value):
            normalized = _key_name(key)
            current_path = f"{path}.{key}" if path and key else (key or path)
            if normalized in _SENSITIVE_KEYS and _has_sensitive_value(nested):
                raise SensitiveInformationError(
                    f"briefing contains sensitive information at {current_path}"
                )
            walk(nested, current_path)

    walk(payload)


def _relation_value(record: object) -> object:
    for name in _RELATION_KEYS:
        value = _field(record, name, _MISSING)
        if _value_present(value):
            return value
    metadata = _first(record, "metadata", "metadata_json", default={})
    for name in _RELATION_KEYS:
        value = _field(metadata, name, _MISSING)
        if _value_present(value):
            return value
    return _MISSING


def _has_relationship(record: object, section: object) -> bool:
    if _relation_value(record) is not _MISSING:
        return True
    relationships = _first(
        section,
        "relationships",
        "relationship_map",
        "deduplication_notes",
        default=_MISSING,
    )
    return _value_present(relationships)


def _identities(record: object) -> tuple[tuple[str, str], ...]:
    candidate = _first(
        record,
        "strategy_candidate_id",
        "candidate_id",
        default=_MISSING,
    )
    strategy = _first(record, "strategy_id", default=_MISSING)
    field_name = _first(record, "field_name", default=_MISSING)
    identities: list[tuple[str, str]] = []
    if _filled(candidate):
        candidate_key = str(candidate).strip()
        if _filled(field_name):
            identities.append(("candidate-field", f"{candidate_key}:{field_name}"))
        else:
            identities.append(("candidate", candidate_key))
    if _filled(strategy):
        strategy_key = str(strategy).strip()
        if _filled(field_name):
            identities.append(("strategy-field", f"{strategy_key}:{field_name}"))
        else:
            identities.append(("strategy", strategy_key))
    if identities:
        return tuple(identities)
    name = _first(record, "canonical_name", "strategy_name", default=_MISSING)
    if _filled(name):
        return (("name", " ".join(str(name).lower().split())),)
    item_id = _first(record, "briefing_item_id", default=_MISSING)
    if _filled(item_id):
        return (("item", str(item_id).strip()),)
    return ()


def _assert_deduplicated_or_related(
    section: object,
    items: Sequence[object],
) -> None:
    for record in (section, *items):
        for name in (
            "deduplicated",
            "duplicates_removed",
            "duplicate_removed",
            "deduplication_complete",
        ):
            marker = _field(record, name, _MISSING)
            if marker is not _MISSING and _is_false(marker):
                raise BriefingRelationshipError(
                    f"briefing declares incomplete deduplication at {name}"
                )

    groups: dict[tuple[str, str], list[object]] = {}
    for record in items:
        for identity in _identities(record):
            groups.setdefault(identity, []).append(record)
    for identity, records in groups.items():
        if len(records) > 1 and not any(
            _has_relationship(record, section) for record in records
        ):
            raise BriefingRelationshipError(
                f"duplicate briefing items require a relationship: {identity[1]!r}"
            )


def _approved(record: object) -> bool:
    marker = _first(record, "approved", "is_approved", default=_MISSING)
    if marker is True:
        return True
    status = _first(
        record,
        "review_status",
        "publication_status",
        "status",
        default=_MISSING,
    )
    if status is _MISSING:
        # A raw BriefingItem row (as built by build_briefing/run_cycle, as
        # opposed to a hand-built payload dict) carries no review_status of
        # its own -- approval lives on the linked StrategyCandidate.
        candidate = _field(record, "strategy_candidate", None)
        if candidate is not None:
            status = _first(
                candidate,
                "review_status",
                "publication_status",
                "status",
                default=_MISSING,
            )
    return isinstance(status, str) and status.strip().lower() in {
        "approved",
        "published",
    }


def _is_internal_draft(section: object) -> bool:
    values: list[object] = []
    for key, nested in _pairs(section):
        if _key_name(key) in _INTERNAL_MARKER_KEYS:
            values.append(nested)
    for name in ("audience", "delivery_scope", "publication_audience"):
        values.append(_first(section, name, default=_MISSING))
    metadata = _first(section, "metadata", "metadata_json", default={})
    for key, nested in _pairs(metadata):
        if _key_name(key) in _INTERNAL_MARKER_KEYS:
            values.append(nested)
    for value in values:
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in {
            "internal",
            "internal_only",
            "internal-draft",
            "private",
            "review",
            "reviewer",
        }:
            return True
    return False


def _assert_approval_or_internal_draft(
    section: object,
    items: Sequence[object],
) -> None:
    if _is_internal_draft(section):
        return
    status = _first(section, "publication_status", "status", default=_MISSING)
    if status is _MISSING or not isinstance(status, str) or not status.strip():
        raise BriefingApprovalError(
            "briefing must record approved status or explicitly be an internal draft"
        )
    if isinstance(status, str) and status.strip().lower() not in {
        "approved",
        "published",
    }:
        raise BriefingApprovalError(
            "briefing must be approved or explicitly marked as an internal draft"
        )
    for index, item in enumerate(items):
        if not _approved(item):
            raise BriefingApprovalError(
                f"briefing item at index {index} is not approved"
            )


def _delivery_values(
    section: object, settings: object
) -> tuple[Any, Any, Any, Any, Any, Any]:
    delivery = _first(section, "delivery", default={})
    briefing_id = _first(section, "briefing_id", default=_MISSING)
    channel = _first(
        section,
        "channel",
        default=_setting(settings, "DELIVERY_CHANNEL", _MISSING),
    )
    content_hash = _first(section, "content_hash", default=_MISSING)
    if content_hash is _MISSING:
        content_hash = _first(delivery, "content_hash", default=_MISSING)
    idempotency_key = _first(section, "idempotency_key", default=_MISSING)
    if idempotency_key is _MISSING:
        idempotency_key = _first(delivery, "idempotency_key", default=_MISSING)
    resend_reason = _first(section, "resend_reason", default=_MISSING)
    if resend_reason is _MISSING:
        resend_reason = _first(delivery, "resend_reason", default=None)
    resend_approved_by = _first(section, "resend_approved_by", default=_MISSING)
    if resend_approved_by is _MISSING:
        resend_approved_by = _first(delivery, "resend_approved_by", default=None)
    return (
        briefing_id,
        channel,
        content_hash,
        idempotency_key,
        resend_reason,
        resend_approved_by,
    )


def _matching_history(
    history: object,
    *,
    expected_key: str | None,
) -> object:
    if expected_key is None:
        return history
    if isinstance(history, Mapping) or hasattr(history, "status"):
        history_key = _field(history, "idempotency_key", _MISSING)
        return history if history_key in {_MISSING, expected_key} else []
    if isinstance(history, (str, bytes, bytearray)):
        return history
    try:
        records = list(history)  # type: ignore[arg-type]
    except TypeError:
        return history
    keyed = [
        record
        for record in records
        if _field(record, "idempotency_key", _MISSING) in {_MISSING, expected_key}
    ]
    return keyed


def _assert_delivery_history(
    section: object,
    settings: object,
    delivery_history: object,
) -> None:
    values = _delivery_values(section, settings)
    briefing_id, channel, content_hash, supplied_key, resend_reason, resend_approved_by = values
    expected_key: str | None = None
    if (
        _filled(briefing_id)
        and _filled(channel)
        and _filled(content_hash)
    ):
        expected_key = delivery_guard.make_idempotency_key(
            str(briefing_id), str(channel), str(content_hash)
        )
        if _filled(supplied_key) and str(supplied_key) != expected_key:
            raise delivery_guard.InvalidIdempotencyKey(
                "idempotency_key does not match briefing_id, channel, and content_hash"
            )
    elif _filled(supplied_key):
        delivery_guard.validate_idempotency_key(str(supplied_key))

    history = _matching_history(delivery_history, expected_key=expected_key)
    # ``next_attempt_no`` is the single source of truth for resend policy.
    # Its exceptions intentionally escape so a failed gate cannot reach a
    # connector through a second path.
    delivery_guard.next_attempt_no(
        history,
        resend_reason=None if resend_reason is _MISSING else resend_reason,
        resend_approved_by=(
            None if resend_approved_by is _MISSING else resend_approved_by
        ),
    )


def _validate_publication_contract(
    payload: object,
    items: Sequence[object],
    *,
    quote_limit: int,
) -> None:
    """Run all existing publication validators at their public boundaries."""

    validator = gate.PublicationGate(
        max_quotes=gate.MAX_EVIDENCE_QUOTES,
        max_quote_chars=quote_limit,
    )
    # Keep the function, item, and reusable-class entry points exercised.  No
    # local copy of their Evidence, link, full-text, or phrase rules exists.
    gate.validate_publication(
        payload,
        max_quotes=gate.MAX_EVIDENCE_QUOTES,
        max_quote_chars=quote_limit,
    )
    for index, item in enumerate(items):
        gate.validate_briefing_item(
            item,
            max_quotes=gate.MAX_EVIDENCE_QUOTES,
            max_quote_chars=quote_limit,
            item_index=index,
        )
    validator.validate(payload)


def gate_briefing(
    briefing_payload: object,
    *,
    settings: object,
    delivery_history: object | Iterable[object] | None = None,
) -> object:
    """Validate one briefing before any external delivery is attempted.

    The original payload is returned unchanged on success.  Any existing
    publication or delivery-guard exception, or a briefing-level condition
    exception, stops the caller before rendering/delivery can continue.
    """

    section = _section(briefing_payload)
    items = _items(briefing_payload)
    try:
        configured_quote_limit = int(
            _setting(settings, "quote_max_chars", gate.MAX_QUOTE_CHARS)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("quote_max_chars must be an integer") from exc
    if configured_quote_limit < 1:
        raise ValueError("quote_max_chars must be positive")
    quote_limit = min(configured_quote_limit, gate.MAX_QUOTE_CHARS)

    _validate_publication_contract(
        briefing_payload,
        items,
        quote_limit=quote_limit,
    )
    _assert_sources_allowed(briefing_payload, items)
    _require_data_window(section)
    _assert_deduplicated_or_related(section, items)
    _assert_approval_or_internal_draft(section, items)
    _assert_no_sensitive_information(briefing_payload)
    _assert_delivery_history(section, settings, delivery_history)
    return briefing_payload


validate_briefing = gate_briefing
gate_publication = gate_briefing


__all__ = [
    "BriefingApprovalError",
    "BriefingGateError",
    "BriefingPublicationError",
    "BriefingRelationshipError",
    "BriefingSourceError",
    "BriefingWindowError",
    "DuplicateBriefingItemError",
    "InvalidBriefingError",
    "MissingDataWindowError",
    "PublicationBriefingGateError",
    "SensitiveInformationError",
    "SourcePolicyError",
    "UnapprovedBriefingError",
    "gate_briefing",
    "gate_publication",
    "validate_briefing",
]
