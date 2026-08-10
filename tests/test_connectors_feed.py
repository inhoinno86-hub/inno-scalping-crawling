from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scalping_briefing.net.transport import TransportResponse
from scalping_briefing.sources.connectors.github import (
    GITHUB_API_TOKEN_ENV,
    GitHubConnector,
)
from scalping_briefing.sources.connectors.html_docs import HTMLDocumentConnector
from scalping_briefing.sources.connectors.json_meta import JSONMetadataConnector
from scalping_briefing.sources.connectors.rss_atom import RSSAtomConnector
from scalping_briefing.sources.registry import (
    SourceInactiveError,
    SourceRegistry,
)
from scalping_briefing.sources.window import calculate_collection_window

from conftest import build_fixture_only_registry


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "sources"
UTC = timezone.utc


class ResponseQueue:
    def __init__(self, *responses: TransportResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers=None, timeout=None) -> TransportResponse:
        self.calls.append((url, dict(headers or {})))
        return self.responses.pop(0)


def response(path: Path, *, content_type: str, status: int = 200, **headers: str) -> TransportResponse:
    response_headers = {"content-type": content_type, **headers}
    return TransportResponse(status, str(path), response_headers, path.read_bytes())


def empty_response(*, status: int, content_type: str, **headers: str) -> TransportResponse:
    return TransportResponse(status, "https://example.invalid/feed", {"content-type": content_type, **headers}, b"")


def test_registry_uses_policy_connector_mapping_and_rejects_inactive_before_request() -> None:
    registry = build_fixture_only_registry()
    assert isinstance(registry.connector_for("fixture_rss_blog"), RSSAtomConnector)
    assert isinstance(registry.connector_for("real_arxiv_api"), RSSAtomConnector)
    assert isinstance(registry.connector_for("fixture_github_repo"), GitHubConnector)
    assert isinstance(registry.connector_for("real_github_api"), GitHubConnector)
    assert isinstance(registry.connector_for("fixture_exchange_docs"), HTMLDocumentConnector)
    assert isinstance(registry.connector_for("real_exchange_docs"), HTMLDocumentConnector)
    with pytest.raises(SourceInactiveError, match="active is false"):
        registry.collect("real_arxiv_api", transport=ResponseQueue())


def _github_transport() -> ResponseQueue:
    fixture_dir = FIXTURES / "fixture_github_repo"
    return ResponseQueue(
        response(fixture_dir / "releases.json", content_type="application/json"),
        response(fixture_dir / "readme.json", content_type="application/json"),
    )


def test_github_connector_adds_bearer_authorization_header_when_token_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GITHUB_API_TOKEN_ENV, "test-only-token-value")
    transport = _github_transport()
    connector = GitHubConnector(
        {"source_id": "test_github_repo", "base_url": "https://api.github.com/repos/example/x"},
        transport,
    )

    connector.collect()

    assert len(transport.calls) == 2
    for _url, headers in transport.calls:
        assert headers["Authorization"] == "Bearer test-only-token-value"


def test_github_connector_omits_authorization_and_warns_when_token_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv(GITHUB_API_TOKEN_ENV, raising=False)
    transport = _github_transport()
    connector = GitHubConnector(
        {"source_id": "test_github_repo", "base_url": "https://api.github.com/repos/example/x"},
        transport,
    )

    with caplog.at_level(logging.WARNING):
        connector.collect()

    assert len(transport.calls) == 2
    for _url, headers in transport.calls:
        assert "Authorization" not in headers
    assert any(
        record.message == "github_api_token_missing" for record in caplog.records
    )
    # The warning may name the missing env var but must never contain a token value.
    assert "Bearer" not in caplog.text


def test_rss_fixture_parses_and_deduplicates_with_initial_lookback() -> None:
    registry = SourceRegistry()
    result = registry.collect("fixture_rss_blog")

    assert result.metadata["feed_format"] == "rss_2_0"
    assert len(result.items) == 2
    assert result.duplicate_count == 1
    assert result.window.initial_lookback_days == 14
    assert result.window.max_lookback_days == 30
    assert {item["canonical_url"] for item in result.items} == {
        "https://example.invalid/research/spread-reversion",
        "https://example.invalid/research/queue-momentum",
    }


def test_atom_conditional_headers_cursor_advance_and_304() -> None:
    first_path = FIXTURES / "fixture_atom_research" / "response.xml"
    second_path = FIXTURES / "fixture_atom_research" / "response.v2.xml"
    transport = ResponseQueue(
        response(
            first_path,
            content_type="application/atom+xml",
            etag='"fixture-atom-v1"',
            **{"last-modified": "Wed, 01 Jul 2026 01:05:00 GMT"},
        ),
        response(
            second_path,
            content_type="application/atom+xml",
            etag='"fixture-atom-v2"',
            **{"last-modified": "Thu, 02 Jul 2026 01:05:00 GMT"},
        ),
        empty_response(
            status=304,
            content_type="application/atom+xml",
            etag='"fixture-atom-v2"',
            **{"last-modified": "Thu, 02 Jul 2026 01:05:00 GMT"},
        ),
    )
    connector = SourceRegistry().connector_for("fixture_atom_research", transport=transport)

    first = connector.collect()
    second = connector.collect(cursor=first.cursor)
    unchanged = connector.collect(cursor=second.cursor)

    assert first.cursor["ETag"] == '"fixture-atom-v1"'
    assert second.cursor["etag"] == '"fixture-atom-v2"'
    assert second.items[0]["summary"].endswith("refreshed conditional-request metadata.")
    assert transport.calls[1][1] == {
        "If-None-Match": '"fixture-atom-v1"',
        "If-Modified-Since": "Wed, 01 Jul 2026 01:05:00 GMT",
    }
    assert unchanged.not_modified is True
    assert unchanged.items == []
    assert unchanged.new_version is False


def test_connector_reuses_advanced_cursor_when_next_collect_omits_cursor() -> None:
    path = FIXTURES / "fixture_atom_research" / "response.xml"
    transport = ResponseQueue(
        response(
            path,
            content_type="application/atom+xml",
            etag='"fixture-atom-v1"',
            **{"last-modified": "Wed, 01 Jul 2026 01:05:00 GMT"},
        ),
        empty_response(
            status=304,
            content_type="application/atom+xml",
            etag='"fixture-atom-v1"',
            **{"last-modified": "Wed, 01 Jul 2026 01:05:00 GMT"},
        ),
    )
    connector = SourceRegistry().connector_for("fixture_atom_research", transport=transport)

    connector.collect()
    unchanged = connector.collect()

    assert transport.calls[1][1] == {
        "If-None-Match": '"fixture-atom-v1"',
        "If-Modified-Since": "Wed, 01 Jul 2026 01:05:00 GMT",
    }
    assert unchanged.not_modified is True


def test_json_metadata_preserves_doi_authors_and_license() -> None:
    path = FIXTURES / "fixture_paper_meta" / "response.json"
    source = SourceRegistry().get_source("fixture_paper_meta")
    result = JSONMetadataConnector(
        source,
        ResponseQueue(response(path, content_type="application/json")),
    ).collect()

    item = result.items[0]
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload["message"]
    assert item["doi"] == expected["DOI"]
    assert item["authors"] == expected["author"]
    assert item["license"] == expected["license"]
    assert item["metadata"]["doi"] == expected["DOI"]


def test_recovery_window_is_capped_and_records_truncation() -> None:
    end = datetime(2026, 8, 2, tzinfo=UTC)
    old_success = end - timedelta(days=45)
    window = calculate_collection_window(
        cursor={"window_end": old_success.isoformat()},
        now=end,
        recovery=True,
    )

    assert window.window_start == end - timedelta(days=30)
    assert window.requested_start == old_success
    assert window.truncated is True
    assert window.truncation is not None
    assert window.as_dict()["truncation_note"]


def test_recovery_without_cursor_caps_configured_initial_lookback() -> None:
    end = datetime(2026, 8, 2, tzinfo=UTC)
    window = calculate_collection_window(
        now=end,
        initial_lookback_days=45,
        recovery=True,
    )

    assert window.window_start == end - timedelta(days=30)
    assert window.requested_start == end - timedelta(days=45)
    assert window.truncated is True


def test_explicit_collection_window_filters_old_entries_and_records_metadata() -> None:
    end = datetime(2026, 7, 2, 12, tzinfo=UTC)
    window = calculate_collection_window(now=end, initial_lookback_days=1)
    result = SourceRegistry().collect("fixture_rss_blog", window=window)

    assert result.metadata["window"]["initial_lookback_days"] == 1
    assert result.items == []
