"""Pure publication-time validation for bounded briefing records.

The gate accepts only the renderer-facing briefing contract: summaries,
bounded Evidence quotes, and links back to original source material.  It does
not read source files, render Markdown, send messages, or create any delivery
or execution path.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any, Final
from urllib.parse import urlsplit

from .phrase_lint import BannedPhraseError, BannedPhraseMatch, lint_text


MAX_EVIDENCE_QUOTES: Final[int] = 2
MAX_QUOTE_CHARS: Final[int] = 300
_MISSING: Final[object] = object()


class PublicationGateError(ValueError):
    """Base error for a briefing that is not safe to publish."""


class MissingEvidenceError(PublicationGateError):
    """A publishable briefing item has no Evidence records."""


class EvidenceQuoteError(PublicationGateError):
    """Evidence count, quote type, or quote length violates the contract."""


class OriginalSourceLinkError(PublicationGateError):
    """A briefing item has no safe link back to source material."""


class OriginalFullTextError(PublicationGateError):
    """Original or normalized full source text was supplied to the gate."""


class PublicationPhraseError(PublicationGateError, BannedPhraseError):
    """A banned phrase was found in text that would be published.

    The error is also a :class:`BannedPhraseError`, preserving the existing
    phrase-lint boundary for callers that already catch that exception.
    """

    def __init__(self, match: BannedPhraseMatch, *, field: str | None = None) -> None:
        self.match = match
        self.field = field
        BannedPhraseError.__init__(self, match)
        if field:
            self.args = (
                f"banned publishing phrase ({match.category}) in {field}: "
                f"{match.phrase!r}",
            )


# Compatibility spellings make the gate usable without exposing implementation
# details to callers that name the validation error differently.
PublicationValidationError = PublicationGateError
PublishGateError = PublicationGateError
PublishingGateError = PublicationGateError
MissingSourceLinkError = OriginalSourceLinkError
FullTextRejectedError = OriginalFullTextError


@dataclass(frozen=True, slots=True)
class PublicationValidation:
    """Small immutable result for callers that prefer an explicit check."""

    accepted: bool = True
    item_count: int = 0

    @property
    def valid(self) -> bool:
        return self.accepted


_FULL_TEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "body",
        "bodytext",
        "content",
        "documentbody",
        "documentcontent",
        "documenttext",
        "fullbody",
        "fulltext",
        "html",
        "htmlbody",
        "normalizedbody",
        "normalizedhtml",
        "normalizedtext",
        "originaldocument",
        "originaldocumentbody",
        "originalbody",
        "originalfulltext",
        "originaltext",
        "rawbody",
        "rawhtml",
        "rawtext",
        "sourcebody",
        "sourcetext",
    }
)
_RELATION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "briefing",
        "briefingitem",
        "briefingitems",
        "document",
        "documentversion",
        "evidence",
        "items",
        "source",
        "strategycandidate",
    }
)
_NON_RENDERED_RELATIONS: Final[frozenset[str]] = frozenset(
    {
        "briefing",
        "briefingitem",
        "briefingitems",
        "document",
        "documentversion",
        "source",
        "strategycandidate",
    }
)
_NON_RENDERED_TEXT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "accessdecisionreason",
        "capturedat",
        "canonicalurl",
        "changehash",
        "contenthash",
        "createdat",
        "documentversionid",
        "evidenceid",
        "fieldstatus",
        "license",
        "metadata",
        "metadatajson",
        "normalizedlocation",
        "originallink",
        "originalurl",
        "rawlocation",
        "retrievedat",
        "robotsallowed",
        "robotsevaluatedat",
        "robotsrulematched",
        "sourceid",
        "sourceurl",
        "sourceversionref",
        "strategycandidateid",
        "strategyid",
        "updatedat",
    }
)


def _key_name(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _field(record: object, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _object_fields(record: object) -> Iterable[tuple[str, object]]:
    values = getattr(record, "__dict__", None)
    if isinstance(values, Mapping):
        for key, value in values.items():
            if not str(key).startswith("_"):
                yield str(key), value


def _nested_values(record: object) -> Iterable[tuple[str, object]]:
    if isinstance(record, Mapping):
        yield from ((str(key), value) for key, value in record.items())
        return
    if isinstance(record, (str, bytes, bytearray)):
        return
    if isinstance(record, Sequence):
        yield from (("", value) for value in record)
        return
    yield from _object_fields(record)


def _reject_full_text(value: object, *, path: str = "", seen: set[int] | None = None) -> None:
    """Reject raw body-shaped fields before any renderer-facing validation."""

    if seen is None:
        seen = set()
    if isinstance(value, (str, bytes, bytearray, int, float, bool, type(None))):
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)

    for key, nested in _nested_values(value):
        normalized = _key_name(key)
        current_path = f"{path}.{key}" if path and key else (key or path)
        if normalized in _FULL_TEXT_KEYS:
            raise OriginalFullTextError(
                f"publication input contains original/full source text at "
                f"{current_path or normalized}"
            )
        _reject_full_text(nested, path=current_path, seen=seen)


def _records(value: object, *, name: str) -> list[object]:
    if value is None:
        return []
    if isinstance(value, Mapping) or not isinstance(
        value, (str, bytes, bytearray, Sequence)
    ):
        return [value]
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{name} must be a record or iterable of records") from exc


def _filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_source_link(value: object) -> bool:
    if not _filled(value):
        return False
    try:
        parsed = urlsplit(str(value).strip())
    except ValueError:
        return False
    if parsed.scheme.lower() in {"javascript", "data"}:
        return False
    return bool(
        parsed.scheme
        and (
            parsed.netloc
            or parsed.scheme.lower() in {"doi", "fixture", "mailto"}
        )
    )


def _source_link_values(record: object) -> list[object]:
    values: list[object] = []
    for name in (
        "source_url",
        "original_url",
        "source_link",
        "original_source_url",
        "original_source_link",
        "original_link",
    ):
        candidate = _field(record, name, _MISSING)
        if candidate is not _MISSING:
            values.append(candidate)

    document_version = _field(record, "document_version", None)
    document = _field(document_version, "document", None)
    for nested in (document_version, document):
        if nested is None:
            continue
        for name in ("source_url", "original_url", "canonical_url"):
            candidate = _field(nested, name, _MISSING)
            if candidate is not _MISSING:
                values.append(candidate)
    return values


def _item_identifier(item: object, index: int) -> str:
    value = _field(item, "briefing_item_id", None)
    return str(value) if _filled(value) else f"item[{index}]"


def _text_is_rendered(key: str) -> bool:
    normalized = _key_name(key)
    if normalized in _NON_RENDERED_TEXT_KEYS or normalized in _RELATION_KEYS:
        return False
    if normalized.endswith(("id", "at", "url", "hash", "location")):
        return False
    return True


def _lint_renderer_text(
    value: object,
    *,
    path: str = "",
    key: str = "",
    seen: set[int] | None = None,
) -> None:
    """Apply the established phrase lint to text that can reach publication."""

    if seen is None:
        seen = set()
    if isinstance(value, str):
        if _text_is_rendered(key):
            try:
                lint_text(value)
            except BannedPhraseError as exc:
                raise PublicationPhraseError(exc.match, field=path or key) from exc
        return
    if isinstance(value, (bytes, bytearray, int, float, bool, type(None))):
        return
    identity = id(value)
    if identity in seen:
        return
    seen.add(identity)
    for child_key, nested in _nested_values(value):
        normalized = _key_name(child_key)
        if (
            normalized in _FULL_TEXT_KEYS
            or normalized in _NON_RENDERED_TEXT_KEYS
            or normalized in _NON_RENDERED_RELATIONS
        ):
            continue
        child_path = f"{path}.{child_key}" if path and child_key else (child_key or path)
        _lint_renderer_text(
            nested,
            path=child_path,
            key=child_key,
            seen=seen,
        )


def validate_briefing_item(
    item: object,
    *,
    max_quotes: int = MAX_EVIDENCE_QUOTES,
    max_quote_chars: int = MAX_QUOTE_CHARS,
    item_index: int = 0,
    max_evidence_quotes: int | None = None,
    quote_max_chars: int | None = None,
) -> object:
    """Validate one renderer-facing briefing item and return it unchanged."""

    if max_evidence_quotes is not None:
        if max_quotes != MAX_EVIDENCE_QUOTES and max_quotes != max_evidence_quotes:
            raise TypeError("use max_quotes or max_evidence_quotes, not both")
        max_quotes = max_evidence_quotes
    if quote_max_chars is not None:
        if max_quote_chars != MAX_QUOTE_CHARS and max_quote_chars != quote_max_chars:
            raise TypeError("use max_quote_chars or quote_max_chars, not both")
        max_quote_chars = quote_max_chars
    if max_quotes > MAX_EVIDENCE_QUOTES or max_quote_chars > MAX_QUOTE_CHARS:
        raise ValueError("publication limits cannot be wider than the Phase 1 contract")
    if max_quotes < 1 or max_quote_chars < 1:
        raise ValueError("publication limits must be positive")

    _reject_full_text(item)
    identifier = _item_identifier(item, item_index)
    evidence = _records(_field(item, "evidence", None), name=f"{identifier}.evidence")
    if not evidence:
        raise MissingEvidenceError(
            f"briefing item {identifier!r} requires at least one Evidence record"
        )
    if len(evidence) > max_quotes:
        raise EvidenceQuoteError(
            f"briefing item {identifier!r} contains {len(evidence)} Evidence records; "
            f"at most {max_quotes} are publishable"
        )

    has_original_link = False
    for evidence_index, record in enumerate(evidence):
        document_version_id = _field(record, "document_version_id", None)
        if not _filled(document_version_id):
            raise PublicationGateError(
                f"Evidence {identifier}[{evidence_index}] requires document_version_id"
            )

        quote = _field(record, "quote", None)
        if not isinstance(quote, str) or not quote:
            raise EvidenceQuoteError(
                f"Evidence {identifier}[{evidence_index}] quote must be non-empty text"
            )
        if len(quote) > max_quote_chars:
            raise EvidenceQuoteError(
                f"Evidence {identifier}[{evidence_index}] quote exceeds "
                f"{max_quote_chars} characters"
            )

        link_values = _source_link_values(record)
        for link in link_values:
            if _filled(link) and not _safe_source_link(link):
                raise OriginalSourceLinkError(
                    f"Evidence {identifier}[{evidence_index}] has an unsafe or invalid "
                    "original source link"
                )
        if any(_safe_source_link(link) for link in link_values):
            has_original_link = True

    for name in (
        "source_url",
        "original_url",
        "source_link",
        "original_source_url",
        "original_source_link",
        "original_link",
    ):
        link = _field(item, name, None)
        if _filled(link) and not _safe_source_link(link):
            raise OriginalSourceLinkError(
                f"briefing item {identifier!r} has an unsafe or invalid original source link"
            )
        if _safe_source_link(link):
            has_original_link = True
    if not has_original_link:
        raise OriginalSourceLinkError(
            f"briefing item {identifier!r} requires an original source link"
        )

    _lint_renderer_text(item, path=identifier)
    return item


def _publication_items(publication: object) -> list[object]:
    if isinstance(publication, Sequence) and not isinstance(
        publication, (str, bytes, bytearray)
    ):
        return list(publication)
    items = _field(publication, "items", _MISSING)
    if items is not _MISSING:
        return _records(items, name="publication.items")
    if _field(publication, "evidence", _MISSING) is not _MISSING:
        return [publication]
    raise PublicationGateError("publication input must contain briefing items")


def validate_publication(
    publication: object,
    *,
    max_quotes: int = MAX_EVIDENCE_QUOTES,
    max_quote_chars: int = MAX_QUOTE_CHARS,
    max_evidence_quotes: int | None = None,
    quote_max_chars: int | None = None,
) -> object:
    """Validate a briefing or item without accepting source full text.

    The original object is returned on success.  No body is loaded, copied, or
    transformed; callers can pass the validated contract to their own later
    renderer while the gate itself remains Phase 1 policy only.
    """

    if max_evidence_quotes is not None:
        if max_quotes != MAX_EVIDENCE_QUOTES and max_quotes != max_evidence_quotes:
            raise TypeError("use max_quotes or max_evidence_quotes, not both")
        max_quotes = max_evidence_quotes
    if quote_max_chars is not None:
        if max_quote_chars != MAX_QUOTE_CHARS and max_quote_chars != quote_max_chars:
            raise TypeError("use max_quote_chars or quote_max_chars, not both")
        max_quote_chars = quote_max_chars

    _reject_full_text(publication)
    items = _publication_items(publication)
    for index, item in enumerate(items):
        validate_briefing_item(
            item,
            max_quotes=max_quotes,
            max_quote_chars=max_quote_chars,
            item_index=index,
        )
    _lint_renderer_text(publication, path="publication")
    return publication


def assert_publishable(publication: object, **limits: int) -> object:
    """Explicit assertion alias for :func:`validate_publication`."""

    return validate_publication(publication, **limits)


def validate_for_publication(publication: object, **limits: int) -> object:
    """Compatibility alias for :func:`validate_publication`."""

    return validate_publication(publication, **limits)


def gate_publication(publication: object, **limits: int) -> object:
    """Compatibility alias for :func:`validate_publication`."""

    return validate_publication(publication, **limits)


def is_publishable(publication: object, **limits: int) -> bool:
    """Return a boolean gate result without hiding validation errors elsewhere."""

    try:
        validate_publication(publication, **limits)
    except (PublicationGateError, TypeError, ValueError):
        return False
    return True


can_publish = is_publishable


class PublicationGate:
    """Reusable validator carrying the fixed Phase 1 publication limits."""

    def __init__(
        self,
        *,
        max_quotes: int = MAX_EVIDENCE_QUOTES,
        max_quote_chars: int = MAX_QUOTE_CHARS,
        max_evidence_quotes: int | None = None,
        quote_max_chars: int | None = None,
    ) -> None:
        if max_evidence_quotes is not None:
            if max_quotes != MAX_EVIDENCE_QUOTES and max_quotes != max_evidence_quotes:
                raise TypeError("use max_quotes or max_evidence_quotes, not both")
            max_quotes = max_evidence_quotes
        if quote_max_chars is not None:
            if max_quote_chars != MAX_QUOTE_CHARS and max_quote_chars != quote_max_chars:
                raise TypeError("use max_quote_chars or quote_max_chars, not both")
            max_quote_chars = quote_max_chars
        if max_quotes > MAX_EVIDENCE_QUOTES or max_quote_chars > MAX_QUOTE_CHARS:
            raise ValueError("publication limits cannot be wider than the Phase 1 contract")
        self.max_quotes = max_quotes
        self.max_quote_chars = max_quote_chars

    def validate(self, publication: object) -> object:
        return validate_publication(
            publication,
            max_quotes=self.max_quotes,
            max_quote_chars=self.max_quote_chars,
        )

    def validate_item(self, item: object, *, item_index: int = 0) -> object:
        return validate_briefing_item(
            item,
            max_quotes=self.max_quotes,
            max_quote_chars=self.max_quote_chars,
            item_index=item_index,
        )

    validate_briefing = validate

    check = validate
    __call__ = validate


__all__ = [
    "EvidenceQuoteError",
    "FullTextRejectedError",
    "MAX_EVIDENCE_QUOTES",
    "MAX_QUOTE_CHARS",
    "MissingEvidenceError",
    "MissingSourceLinkError",
    "OriginalFullTextError",
    "OriginalSourceLinkError",
    "PublicationGate",
    "PublicationGateError",
    "PublicationPhraseError",
    "PublicationValidation",
    "PublicationValidationError",
    "PublishGateError",
    "PublishingGateError",
    "assert_publishable",
    "can_publish",
    "gate_publication",
    "is_publishable",
    "check_publication",
    "validate_briefing_item",
    "validate_briefing",
    "validate_item",
    "validate_for_publication",
    "validate_publication",
]


validate_briefing = validate_publication
validate_item = validate_briefing_item
check_publication = is_publishable
