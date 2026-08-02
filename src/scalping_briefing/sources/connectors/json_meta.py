"""JSON scholarly-metadata connector with the shared source result contract."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Mapping
from urllib.parse import urljoin

from scalping_briefing.sources.registry import (
    ConnectorError,
    ConnectorResult,
    conditional_headers,
    response_body,
    response_cursor,
    response_status,
    settings_value,
    source_id,
    source_value,
    update_source_cursor,
)
from scalping_briefing.sources.window import calculate_collection_window


UTC = timezone.utc


def _date_parts(value: Any) -> datetime | None:
    if isinstance(value, Mapping):
        value = value.get("date-parts") or value.get("date_parts")
    if isinstance(value, list) and value and isinstance(value[0], list):
        value = value[0]
    if isinstance(value, (list, tuple)) and value:
        try:
            year = int(value[0])
            month = int(value[1]) if len(value) > 1 else 1
            day = int(value[2]) if len(value) > 2 else 1
            return datetime(year, month, day, tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed_text = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(parsed_text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _copy_json(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value


def _metadata_records(payload: Any) -> list[Mapping[str, Any]]:
    value = payload
    if isinstance(value, Mapping) and isinstance(value.get("message"), Mapping):
        value = value["message"]
    if isinstance(value, Mapping) and isinstance(value.get("items"), list):
        return [item for item in value["items"] if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _first(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        return _first(value[0], default) if value else default
    return str(value).strip() if value is not None else default


class JSONMetadataConnector:
    """Collect DOI metadata without discarding authors or license fields."""

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
        selected_window = window
        if selected_window is None:
            selected_window = calculate_collection_window(
                cursor=effective_cursor,
                last_success_at=source_value(self.source, "last_success_at"),
                now=now,
                initial_lookback_days=(
                    initial_lookback_days
                    if initial_lookback_days is not None
                    else settings_value(self.settings, "initial_lookback_days", 14)
                ),
                max_lookback_days=(
                    max_lookback_days
                    if max_lookback_days is not None
                    else settings_value(self.settings, "max_lookback_days", 30)
                ),
                recovery=recovery,
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
            raise ConnectorError(f"metadata response status is not successful: {status}")
        body = response_body(response)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConnectorError("metadata response is not valid UTF-8 JSON") from exc

        records = _metadata_records(payload)
        if not records:
            raise ConnectorError("metadata response contains no object records")
        items = []
        for record in records:
            doi_value = record.get("DOI", record.get("doi"))
            doi = str(doi_value).strip() if doi_value is not None else ""
            title = _first(record.get("title"))
            authors = _copy_json(record.get("author", record.get("authors", [])))
            license_value = _copy_json(record.get("license", record.get("licenses", [])))
            published = _date_parts(
                record.get("published")
                or record.get("published-print")
                or record.get("published_online")
                or record.get("created")
            )
            original = _first(record.get("URL", record.get("url")))
            if not original and doi:
                original = f"https://doi.org/{doi}"
            if not original:
                original = str(source_value(self.source, "original_url", ""))
            canonical = urljoin(str(source_value(self.source, "base_url", "")), original)
            item = {
                "id": doi or canonical,
                "external_id": doi or canonical,
                "canonical_url": canonical,
                "original_url": canonical,
                "url": canonical,
                "title": title or canonical,
                "summary": _first(record.get("abstract")),
                "description": _first(record.get("abstract")),
                "published_at": _iso(published),
                "updated_at": None,
                "doi": doi or None,
                "authors": authors,
                "license": license_value,
                "metadata": {
                    "record_format": "json_metadata",
                    "doi": doi or None,
                    "authors": _copy_json(authors),
                    "license": _copy_json(license_value),
                },
            }
            items.append(item)

        if now is not None or recovery or window is not None:
            filtered = []
            for item in items:
                timestamp = _date_parts(item.get("published_at"))
                if timestamp is None or selected_window.window_start <= timestamp <= selected_window.window_end:
                    filtered.append(item)
            items = filtered
        version_ref = next_cursor.get("etag") if next_cursor is not None else None
        if version_ref is None and next_cursor is not None:
            version_ref = next_cursor.get("last_modified")
        for item in items:
            item["source_id"] = source_id(self.source)
            item["source_version_ref"] = version_ref
        metadata = {
            "conditional_request": bool(request_headers),
            "record_count": len(records),
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


JsonMetadataConnector = JSONMetadataConnector
JSONMetaConnector = JSONMetadataConnector
JsonMetaConnector = JSONMetadataConnector
PaperMetadataConnector = JSONMetadataConnector


__all__ = [
    "JSONMetaConnector",
    "JSONMetadataConnector",
    "JsonMetaConnector",
    "JsonMetadataConnector",
    "PaperMetadataConnector",
]
