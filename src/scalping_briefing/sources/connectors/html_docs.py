"""HTML technical-document connector with mandatory sanitization."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

from scalping_briefing.normalize.sanitize import sanitize_html
from scalping_briefing.sources.registry import (
    ConnectorError,
    ConnectorResult,
    ItemRecord,
    response_body,
    response_cursor,
    response_headers,
    response_status,
    settings_value,
    source_id,
    source_value,
    update_source_cursor,
)


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        self._in_title = tag.lower() == "title"

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.parts.append(data)


def _request(
    transport: Any,
    url: str,
    headers: Mapping[str, str],
    settings: Mapping[str, Any] | Any | None,
) -> Any:
    timeout = settings_value(settings, "request_timeout_seconds", 20)
    get = getattr(transport, "get", None)
    if get is not None:
        try:
            return get(url, headers=headers, timeout=timeout)
        except TypeError:
            try:
                return get(url, headers=headers)
            except TypeError:
                return get(url)
    request = getattr(transport, "request", None)
    if request is not None:
        try:
            return request("GET", url, headers=headers, timeout=timeout)
        except TypeError:
            try:
                return request("GET", url, headers=headers)
            except TypeError:
                return request("GET", url)
    if callable(transport):
        return transport("GET", url, headers)
    raise ConnectorError("transport must provide get() or request()")


def _title(value: str) -> str:
    parser = _TitleParser()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


class HTMLDocumentConnector:
    """Collect one HTML document and sanitize before returning it."""

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

    def collect(
        self,
        *,
        url: str | None = None,
        response_url: str | None = None,
        cursor: Any = None,
        **_kwargs: Any,
    ) -> ConnectorResult:
        effective_cursor = self.cursor
        if effective_cursor is None:
            effective_cursor = source_value(self.source, "cursor")
        if cursor is not None:
            effective_cursor = cursor
        target = response_url or url or str(source_value(self.source, "base_url", ""))
        if not target:
            raise ConnectorError("HTML source requires base_url")
        headers = {"Accept": "text/html,application/xhtml+xml"}
        response = _request(self.transport, target, headers, self.settings)
        status = response_status(response)
        next_cursor = response_cursor(response, effective_cursor)
        if status == 304:
            self.cursor = next_cursor
            update_source_cursor(self.source, next_cursor)
            return ConnectorResult(
                source_id=source_id(self.source),
                cursor=next_cursor,
                status_code=status,
                not_modified=True,
                metadata={
                    "html": True,
                    "sanitized": True,
                    "new_version": False,
                },
            )
        if status < 200 or status >= 300:
            raise ConnectorError(f"HTML response status is not successful: {status}")
        raw_bytes = response_body(response)
        raw_body = raw_bytes.decode("utf-8", errors="replace")
        # This call is deliberately before the result is constructed.  Any
        # normalized body exposed by this connector has crossed the trust gate.
        normalized_body = sanitize_html(raw_body)
        canonical = str(
            source_value(self.source, "original_url", "") or target
        )
        source_version_ref = None
        if next_cursor is not None:
            source_version_ref = next_cursor.get("etag") or next_cursor.get(
                "last_modified"
            )
        title = _title(raw_body) or str(
            source_value(self.source, "name", "") or canonical
        )
        item = ItemRecord(
            {
                "id": canonical,
                "external_id": canonical,
                "canonical_url": canonical,
                "original_url": canonical,
                "url": canonical,
                "title": title,
                "summary": " ".join(normalized_body.split()),
                "description": normalized_body,
                "published_at": None,
                "updated_at": None,
                "authors": [],
                "doi": None,
                "license": source_value(self.source, "license_notes"),
                "metadata": {
                    "html": True,
                    "content_type": next(
                        (
                            str(value)
                            for key, value in response_headers(response).items()
                            if str(key).lower() == "content-type"
                        ),
                        "text/html",
                    ),
                    "sanitized": True,
                    "robots_allowed": source_value(
                        self.source, "robots_allowed", "unknown"
                    ),
                    "robots_rule_matched": source_value(
                        self.source, "robots_rule_matched"
                    ),
                    "access_decision_reason": source_value(
                        self.source, "access_decision_reason"
                    ),
                },
                "raw_body": raw_body,
                "normalized_body": normalized_body,
                "body_hash": f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
                "source_id": source_id(self.source),
                "source_version_ref": source_version_ref,
                "robots_allowed": source_value(
                    self.source, "robots_allowed", "unknown"
                ),
                "robots_rule_matched": source_value(
                    self.source, "robots_rule_matched"
                ),
                "access_decision_reason": source_value(
                    self.source, "access_decision_reason"
                ),
            }
        )
        self.cursor = next_cursor
        update_source_cursor(self.source, next_cursor)
        return ConnectorResult(
            source_id=source_id(self.source),
            items=[item],
            cursor=next_cursor,
            status_code=status,
            metadata={
                "html": True,
                "sanitized": True,
                "new_version": True,
                "raw_bytes": len(raw_bytes),
            },
            content_hash=f"sha256:{hashlib.sha256(normalized_body.encode('utf-8')).hexdigest()}",
        )

    fetch = collect


HTMLConnector = HTMLDocumentConnector
HtmlDocumentConnector = HTMLDocumentConnector


__all__ = ["HTMLConnector", "HTMLDocumentConnector", "HtmlDocumentConnector"]
