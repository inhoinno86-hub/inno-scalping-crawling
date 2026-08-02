"""GitHub release and README connector using the shared source contract."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from scalping_briefing.sources.registry import (
    ConnectorError,
    ConnectorResult,
    CursorState,
    ItemRecord,
    coerce_cursor,
    response_body,
    response_cursor,
    response_status,
    settings_value,
    source_id,
    source_value,
    update_source_cursor,
)


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


def _json_body(response: Any, *, description: str) -> tuple[Any, bytes]:
    body = response_body(response)
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectorError(f"{description} is not valid UTF-8 JSON") from exc
    return value, body


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _endpoint(base: str, suffix: str) -> str:
    return f"{base.rstrip('/')}/{suffix.lstrip('/')}"


def _commit_from_cursor(cursor: CursorState | None) -> str | None:
    if cursor is None:
        return None
    for key in ("commit_sha", "sha", "value"):
        value = cursor.get(key)
        if value is not None and _as_text(value):
            return _as_text(value)
    return None


def _published_sort_key(value: Any) -> str:
    return _as_text(value)


def _release_item(source: Any, release: Mapping[str, Any], commit_sha: str | None) -> ItemRecord:
    release_id = _as_text(release.get("id"))
    tag = _as_text(release.get("tag_name"))
    canonical = _as_text(
        release.get("html_url")
        or release.get("url")
        or source_value(source, "original_url", "")
    )
    external_id = release_id or tag or canonical
    body = _as_text(release.get("body"))
    release_commit = _as_text(
        release.get("target_commitish") or release.get("target_commit")
    ) or commit_sha
    return ItemRecord(
        {
            "id": f"release:{external_id}",
            "external_id": f"release:{external_id}",
            "canonical_url": canonical,
            "original_url": canonical,
            "url": canonical,
            "title": _as_text(release.get("name")) or tag or canonical,
            "summary": body,
            "description": body,
            "published_at": _as_text(release.get("published_at")) or None,
            "updated_at": _as_text(release.get("created_at")) or None,
            "authors": [],
            "doi": None,
            "license": None,
            "metadata": {
                "github_kind": "release",
                "release_id": release_id or None,
                "tag_name": tag or None,
                "commit_sha": release_commit,
            },
            "body": body,
            "raw_record": dict(release),
            "source_id": source_id(source),
            "source_version_ref": release_commit,
        }
    )


def _readme_item(source: Any, readme: Mapping[str, Any], commit_sha: str | None) -> ItemRecord:
    encoded = _as_text(readme.get("content"))
    encoding = _as_text(readme.get("encoding"), "utf-8").lower()
    if encoding == "base64":
        try:
            content = base64.b64decode(encoded, validate=False).decode(
                "utf-8", errors="replace"
            )
        except (ValueError, UnicodeError) as exc:
            raise ConnectorError("GitHub README base64 content is invalid") from exc
    else:
        content = encoded
    canonical = _as_text(
        readme.get("html_url")
        or readme.get("download_url")
        or source_value(source, "original_url", "")
    )
    readme_sha = _as_text(readme.get("sha")) or commit_sha
    path = _as_text(readme.get("path"), "README.md")
    return ItemRecord(
        {
            "id": f"readme:{canonical or path}",
            "external_id": f"readme:{canonical or path}",
            "canonical_url": canonical,
            "original_url": canonical,
            "url": canonical,
            "title": _as_text(readme.get("name"), path),
            "summary": content,
            "description": content,
            "published_at": _as_text(readme.get("captured_at")) or None,
            "updated_at": None,
            "authors": [],
            "doi": None,
            "license": None,
            "metadata": {
                "github_kind": "readme",
                "path": path,
                "commit_sha": readme_sha,
                "encoding": encoding,
            },
            "body": content,
            "raw_record": dict(readme),
            "source_id": source_id(source),
            "source_version_ref": readme_sha,
        }
    )


class GitHubConnector:
    """Collect release and README records with commit-SHA cursors."""

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
        releases_url: str | None = None,
        readme_url: str | None = None,
        cursor: Any = None,
        **_kwargs: Any,
    ) -> ConnectorResult:
        effective_cursor = self.cursor
        if effective_cursor is None:
            effective_cursor = source_value(self.source, "cursor")
        if cursor is not None:
            effective_cursor = cursor
        state = coerce_cursor(effective_cursor)
        base = _as_text(url or source_value(self.source, "base_url", ""))
        if not base:
            raise ConnectorError("GitHub source requires base_url")
        selected_releases_url = releases_url or response_url
        if selected_releases_url is None:
            selected_releases_url = base
        selected_readme_url = readme_url or _endpoint(base, "readme.json")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        releases_response = _request(
            self.transport, selected_releases_url, headers, self.settings
        )
        release_status = response_status(releases_response)
        response_state = response_cursor(releases_response, state)
        if release_status == 304:
            self.cursor = response_state
            update_source_cursor(self.source, response_state)
            return ConnectorResult(
                source_id=source_id(self.source),
                cursor=response_state,
                status_code=release_status,
                not_modified=True,
                metadata={
                    "github": True,
                    "new_version": False,
                    "releases_url": selected_releases_url,
                    "readme_url": selected_readme_url,
                },
            )
        if release_status < 200 or release_status >= 300:
            raise ConnectorError(
                f"GitHub releases response status is not successful: {release_status}"
            )
        releases_payload, releases_body = _json_body(
            releases_response, description="GitHub releases response"
        )
        releases = (
            releases_payload
            if isinstance(releases_payload, list)
            else releases_payload.get("releases")
            if isinstance(releases_payload, Mapping)
            else None
        )
        if not isinstance(releases, list):
            raise ConnectorError("GitHub releases response must contain a list")
        release_records = [
            record for record in releases if isinstance(record, Mapping)
        ]

        readme_response = _request(
            self.transport, selected_readme_url, headers, self.settings
        )
        readme_status = response_status(readme_response)
        if readme_status == 304:
            readme_payload: Mapping[str, Any] | None = None
            readme_body = b""
        elif readme_status < 200 or readme_status >= 300:
            raise ConnectorError(
                f"GitHub README response status is not successful: {readme_status}"
            )
        else:
            parsed_readme, readme_body = _json_body(
                readme_response, description="GitHub README response"
            )
            readme_payload = parsed_readme if isinstance(parsed_readme, Mapping) else None

        latest = max(
            (
                record
                for record in release_records
                if _as_text(
                    record.get("target_commitish") or record.get("target_commit")
                )
            ),
            key=lambda record: _published_sort_key(
                record.get("published_at") or record.get("created_at")
            ),
            default=None,
        )
        latest_sha = (
            _as_text(latest.get("target_commitish") or latest.get("target_commit"))
            if latest is not None
            else _commit_from_cursor(state)
        ) or _commit_from_cursor(response_state)
        next_cursor = response_state or CursorState()
        if latest_sha:
            next_cursor["commit_sha"] = latest_sha
            next_cursor["sha"] = latest_sha
        if latest is not None and latest.get("id") is not None:
            next_cursor["release_id"] = latest.get("id")
        if readme_payload is not None and _as_text(readme_payload.get("sha")):
            next_cursor["readme_sha"] = _as_text(readme_payload.get("sha"))

        items = [_release_item(self.source, record, latest_sha) for record in release_records]
        if readme_payload is not None:
            items.append(_readme_item(self.source, readme_payload, latest_sha))
        for item in items:
            item["source_version_ref"] = latest_sha or item.get("source_version_ref")
        content_hash = hashlib.sha256(releases_body + b"\n" + readme_body).hexdigest()
        metadata = {
            "github": True,
            "release_count": len(release_records),
            "readme_collected": readme_payload is not None,
            "commit_sha": latest_sha,
            "previous_commit_sha": _commit_from_cursor(state),
            "new_version": True,
            "releases_url": selected_releases_url,
            "readme_url": selected_readme_url,
        }
        self.cursor = next_cursor
        update_source_cursor(self.source, next_cursor)
        return ConnectorResult(
            source_id=source_id(self.source),
            items=items,
            cursor=next_cursor,
            status_code=release_status,
            metadata=metadata,
            content_hash=content_hash,
        )

    fetch = collect


GithubConnector = GitHubConnector
GitHubRepositoryConnector = GitHubConnector


__all__ = ["GitHubConnector", "GithubConnector", "GitHubRepositoryConnector"]
