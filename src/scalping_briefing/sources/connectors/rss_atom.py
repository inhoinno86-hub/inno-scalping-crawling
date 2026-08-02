"""RSS 2.0 and Atom connector using only the Python XML standard library."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin
from xml.etree import ElementTree

from scalping_briefing.sources.registry import (
    ConnectorError,
    ConnectorResult,
    CursorState,
    ItemRecord,
    calculate_collection_window,
    conditional_headers,
    response_body,
    response_cursor,
    response_headers,
    response_status,
    settings_value,
    source_id,
    source_metadata,
    source_value,
    update_source_cursor,
)


UTC = timezone.utc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return " ".join(part.strip() for part in element.itertext() if part.strip()).strip()


def _child(element: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    wanted = {name.lower() for name in names}
    for candidate in list(element):
        if _local_name(candidate.tag) in wanted:
            return candidate
    return None


def _children(element: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    wanted = name.lower()
    return [candidate for candidate in element.iter() if _local_name(candidate.tag) == wanted]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed_text = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(parsed_text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _timestamp(item: Mapping[str, Any]) -> datetime | None:
    for key in ("updated_at", "published_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            parsed = _parse_datetime(value)
            if parsed is not None:
                return parsed
    return None


def _canonical_url(source: Any, value: str, identifier: str) -> str:
    candidate = value.strip() if value else identifier.strip()
    if not candidate:
        return str(source_value(source, "base_url", ""))
    return urljoin(str(source_value(source, "base_url", "")), candidate)


def _link(element: ElementTree.Element) -> str:
    href = element.attrib.get("href")
    if href:
        rel = element.attrib.get("rel", "alternate").lower()
        if rel in {"alternate", ""}:
            return href.strip()
    return _text(element)


def _parse_rss_item(source: Any, element: ElementTree.Element) -> ItemRecord:
    guid = _text(_child(element, "guid", "id"))
    link = _text(_child(element, "link"))
    title = _text(_child(element, "title"))
    published_raw = _text(_child(element, "pubdate", "published", "date"))
    updated_raw = _text(_child(element, "updated", "modified"))
    published = _parse_datetime(published_raw)
    updated = _parse_datetime(updated_raw)
    summary = _text(_child(element, "description", "summary", "content", "abstract"))
    identifier = guid or link or title
    canonical = _canonical_url(source, link, identifier)
    return ItemRecord(
        {
            "id": identifier,
            "external_id": identifier,
            "canonical_url": canonical,
            "original_url": canonical,
            "url": canonical,
            "title": title or canonical,
            "summary": summary,
            "description": summary,
            "published_at": _iso(published),
            "updated_at": _iso(updated),
            "authors": [],
            "doi": None,
            "license": None,
            "metadata": {
                "feed_format": "rss_2_0",
                "guid": guid or None,
                "published_raw": published_raw or None,
                "updated_raw": updated_raw or None,
            },
        }
    )


def _parse_atom_entry(source: Any, element: ElementTree.Element) -> ItemRecord:
    identifier = _text(_child(element, "id"))
    link = ""
    for candidate in _children(element, "link"):
        selected = _link(candidate)
        if selected:
            link = selected
            if candidate.attrib.get("rel", "alternate").lower() == "alternate":
                break
    title = _text(_child(element, "title"))
    summary = _text(_child(element, "summary", "content", "description"))
    published_raw = _text(_child(element, "published", "created"))
    updated_raw = _text(_child(element, "updated", "modified"))
    published = _parse_datetime(published_raw)
    updated = _parse_datetime(updated_raw)
    authors: list[dict[str, str] | str] = []
    for author in _children(element, "author"):
        name = _text(_child(author, "name"))
        email = _text(_child(author, "email"))
        if name or email:
            authors.append({"name": name, "email": email} if email else name)
    canonical = _canonical_url(source, link, identifier or link or title)
    return ItemRecord(
        {
            "id": identifier or canonical,
            "external_id": identifier or canonical,
            "canonical_url": canonical,
            "original_url": canonical,
            "url": canonical,
            "title": title or canonical,
            "summary": summary,
            "description": summary,
            "published_at": _iso(published),
            "updated_at": _iso(updated),
            "authors": authors,
            "doi": None,
            "license": None,
            "metadata": {"feed_format": "atom", "published_raw": published_raw or None, "updated_raw": updated_raw or None},
        }
    )


def _deduplicate(items: Iterable[ItemRecord]) -> tuple[list[ItemRecord], int]:
    selected: dict[str, ItemRecord] = {}
    order: list[str] = []
    duplicates = 0
    for item in items:
        # RSS GUID and Atom id are source identities.  Canonical URL is the
        # fallback for feeds that omit either field.
        key = str(
            item.get("external_id")
            or item.get("canonical_url")
            or item.get("id")
            or item.get("title")
        ).strip()
        existing = selected.get(key)
        if existing is None:
            selected[key] = item
            order.append(key)
            continue
        duplicates += 1
        current_time = _timestamp(item)
        existing_time = _timestamp(existing)
        if current_time is not None and (existing_time is None or current_time > existing_time):
            selected[key] = item
    return [selected[key] for key in order], duplicates


def _filter_window(items: list[ItemRecord], window: Any, *, enabled: bool) -> list[ItemRecord]:
    if not enabled:
        return items
    filtered: list[ItemRecord] = []
    for item in items:
        timestamp = _timestamp(item)
        if timestamp is None or window.window_start <= timestamp <= window.window_end:
            filtered.append(item)
    return filtered


class RSSAtomConnector:
    """Collect RSS 2.0 or Atom entries through the shared transport contract."""

    def __init__(
        self,
        source: Mapping[str, Any] | Any,
        transport: Any,
        *,
        settings: Mapping[str, Any] | Any | None = None,
        cursor: Any = None,
    ) -> None:
        self.source = source
        self.transport = transport
        self.settings = settings
        self.cursor = cursor

    def _request(self, url: str, headers: Mapping[str, str]) -> Any:
        timeout = settings_value(self.settings, "request_timeout_seconds", 20)
        get = getattr(self.transport, "get", None)
        if get is not None:
            try:
                return get(url, headers=headers, timeout=timeout)
            except TypeError:
                try:
                    return get(url, headers=headers)
                except TypeError:
                    return get(url)
        request = getattr(self.transport, "request", None)
        if request is not None:
            try:
                return request("GET", url, headers=headers, timeout=timeout)
            except TypeError:
                return request("GET", url, headers=headers)
        if callable(self.transport):
            return self.transport("GET", url, headers)
        raise ConnectorError("transport must provide get() or request()")

    def _window(
        self,
        *,
        cursor: Any,
        window: Any,
        now: datetime | date | str | None,
        recovery: bool,
        initial_lookback_days: int | None,
        max_lookback_days: int | None,
    ) -> tuple[Any, bool]:
        if window is not None:
            return window, True
        selected_initial = (
            initial_lookback_days
            if initial_lookback_days is not None
            else settings_value(self.settings, "initial_lookback_days", 14)
        )
        selected_max = (
            max_lookback_days
            if max_lookback_days is not None
            else settings_value(self.settings, "max_lookback_days", 30)
        )
        return (
            calculate_collection_window(
                cursor=cursor,
                last_success_at=source_value(self.source, "last_success_at"),
                now=now,
                initial_lookback_days=selected_initial,
                max_lookback_days=selected_max,
                recovery=recovery,
            ),
            now is not None or recovery,
        )

    def collect(
        self,
        *,
        url: str | None = None,
        response_url: str | None = None,
        cursor: Any = None,
        window: Any = None,
        now: datetime | date | str | None = None,
        recovery: bool = False,
        initial_lookback_days: int | None = None,
        max_lookback_days: int | None = None,
    ) -> ConnectorResult:
        effective_cursor = self.cursor
        if effective_cursor is None:
            effective_cursor = source_value(self.source, "cursor")
        if cursor is not None:
            effective_cursor = cursor
        target = response_url or url or str(source_value(self.source, "base_url", ""))
        request_headers = conditional_headers(effective_cursor)
        response = self._request(target, request_headers)
        status = response_status(response)
        next_cursor = response_cursor(response, effective_cursor)
        selected_window, filter_items = self._window(
            cursor=effective_cursor,
            window=window,
            now=now,
            recovery=recovery,
            initial_lookback_days=initial_lookback_days,
            max_lookback_days=max_lookback_days,
        )
        if status == 304:
            self.cursor = next_cursor
            update_source_cursor(self.source, next_cursor)
            return ConnectorResult(
                source_id=source_id(self.source),
                cursor=next_cursor,
                status_code=status,
                not_modified=True,
                window=selected_window,
                metadata={
                    "conditional_request": bool(request_headers),
                    "new_version": False,
                    "window": selected_window.as_dict(),
                    "truncated": selected_window.truncated,
                },
            )
        if status < 200 or status >= 300:
            raise ConnectorError(f"feed response status is not successful: {status}")

        body = response_body(response)
        try:
            root = ElementTree.fromstring(body)
        except ElementTree.ParseError as exc:
            raise ConnectorError("feed response is not valid XML") from exc
        root_name = _local_name(root.tag)
        if root_name == "rss":
            raw_items = [_parse_rss_item(self.source, item) for item in _children(root, "item")]
        elif root_name == "feed":
            raw_items = [_parse_atom_entry(self.source, entry) for entry in _children(root, "entry")]
        else:
            raise ConnectorError(f"unsupported feed root element: {root_name!r}")

        items, duplicate_count = _deduplicate(raw_items)
        items = _filter_window(items, selected_window, enabled=filter_items)
        version_ref = next_cursor.get("etag") if next_cursor is not None else None
        if version_ref is None and next_cursor is not None:
            version_ref = next_cursor.get("last_modified")
        for item in items:
            item["source_id"] = source_id(self.source)
            item["source_version_ref"] = version_ref
        metadata = {
            "conditional_request": bool(request_headers),
            "feed_format": "rss_2_0" if root_name == "rss" else "atom",
            "duplicate_count": duplicate_count,
            "raw_item_count": len(raw_items),
            "window": selected_window.as_dict(),
            "new_version": True,
            "truncated": selected_window.truncated,
        }
        if selected_window.truncation is not None:
            metadata["truncation"] = selected_window.truncation
        self.cursor = next_cursor
        update_source_cursor(self.source, next_cursor)
        return ConnectorResult(
            source_id=source_id(self.source),
            items=items,
            cursor=next_cursor,
            status_code=status,
            window=selected_window,
            metadata=metadata,
            content_hash=hashlib.sha256(body).hexdigest(),
        )

    fetch = collect


RssAtomConnector = RSSAtomConnector
FeedConnector = RSSAtomConnector
RSSConnector = RSSAtomConnector
AtomConnector = RSSAtomConnector


__all__ = [
    "AtomConnector",
    "FeedConnector",
    "RSSAtomConnector",
    "RSSConnector",
    "RssAtomConnector",
]
