from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing import run_briefing
from scalping_briefing.models import Base, Document, DocumentVersion, Source
from scalping_briefing.net.guards import HostNotAllowedError
from scalping_briefing.normalize.sanitize import sanitize_html as real_sanitize_html
from scalping_briefing.repository.documents import DocumentRepository
from scalping_briefing.sources.registry import (
    SourceInactiveError,
    SourceRecord,
    SourceRegistry,
)
from scalping_briefing.storage.files import LocalFileStorage


def test_all_five_fixture_sources_collect_with_sockets_blocked(monkeypatch) -> None:
    def blocked_socket(*_args, **_kwargs):
        raise AssertionError("fixture collection attempted a socket call")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    registry = SourceRegistry()
    try:
        active = registry.active_sources
        assert len(active) == 5
        results = {
            source.source_id: registry.collect(source.source_id) for source in active
        }
    finally:
        registry.close()

    assert set(results) == {
        "fixture_rss_blog",
        "fixture_atom_research",
        "fixture_github_repo",
        "fixture_exchange_docs",
        "fixture_paper_meta",
    }
    assert all(result.items for result in results.values())
    assert results["fixture_github_repo"].metadata["github"] is True
    assert results["fixture_exchange_docs"].metadata["sanitized"] is True


def test_github_fixture_cursor_advances_from_v1_to_v2(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "socket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fixture collection attempted a socket call")
        ),
    )
    registry = SourceRegistry()
    try:
        source = registry.get_source("fixture_github_repo")
        first = registry.collect(source.source_id)
        second = registry.collect(
            source.source_id,
            cursor=first.cursor,
            releases_url=f"{source.base_url}/releases.v2.json",
            readme_url=f"{source.base_url}/readme.json",
        )
    finally:
        registry.close()

    assert first.cursor is not None
    assert second.cursor is not None
    assert first.cursor["commit_sha"] == "9fceb02fixture"
    assert second.cursor["commit_sha"] == "b5f2d7f8e9c00123456789abcdef0123456789ab"
    assert second.cursor["commit_sha"] != first.cursor["commit_sha"]
    assert second.metadata["previous_commit_sha"] == first.cursor["commit_sha"]
    assert len(second.items) == 3


def test_html_sanitize_runs_before_normalized_storage(monkeypatch, tmp_path: Path) -> None:
    import scalping_briefing.sources.connectors.html_docs as html_docs

    calls: list[str] = []

    def observed(value):
        calls.append(value)
        return real_sanitize_html(value)

    monkeypatch.setattr(html_docs, "sanitize_html", observed)
    registry = SourceRegistry()
    try:
        source = registry.get_source("fixture_exchange_docs")
        result = registry.collect(source.source_id)
    finally:
        registry.close()

    item = result.items[0]
    assert calls
    assert item["metadata"]["sanitized"] is True
    assert "<script" not in item["normalized_body"].lower()
    assert "javascript:" not in item["normalized_body"].lower()
    assert "onerror" not in item["normalized_body"].lower()
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in item["normalized_body"]

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    storage = LocalFileStorage(
        tmp_path / "storage",
        settings={"raw_retention_days": 1, "normalized_retention_days": "unlimited"},
    )
    with Session(engine, expire_on_commit=False) as session:
        session.add(
            Source(
                source_id=source.source_id,
                name=source.name,
                type=source.type,
                base_url=source.base_url,
                connector_type=source.connector_type,
            )
        )
        session.flush()
        persisted = DocumentRepository(session, storage=storage).ingest_document(
            source_id=source.source_id,
            url=item["original_url"],
            title=item["title"],
            raw_body=item["raw_body"],
            normalized_body=item["normalized_body"],
            content_hash=result.content_hash,
            body_hash=item["body_hash"],
            robots_allowed=True,
        )
        session.commit()

    assert persisted.document_version is not None
    stored = Path(persisted.document_version.normalized_location).read_text()
    assert "<script" not in stored.lower()
    assert "javascript:" not in stored.lower()
    assert "onerror" not in stored.lower()
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in stored


def test_inactive_live_candidates_use_registry_and_are_rejected_before_transport() -> None:
    class NoRequestTransport:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("inactive source attempted transport activity")

    registry = SourceRegistry()
    transport = NoRequestTransport()
    try:
        inactive = [source for source in registry.sources.values() if source.active is False]
        assert inactive
        for source in inactive:
            connector = registry.connector_for(source.source_id, transport=transport)
            assert connector.source is source
            with pytest.raises(SourceInactiveError, match="active is false"):
                registry.collect(source.source_id, transport=transport)
    finally:
        registry.close()

    assert transport.calls == 0


def test_run_briefing_uses_registry_collection_and_evaluates_robots_before_storage(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    def blocked_socket(*_args, **_kwargs):
        raise AssertionError("run_briefing attempted a socket call")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.sqlite3'}")
    collected: list[str] = []
    original_collect = SourceRegistry.collect

    def observed_collect(self, source_id: str, **kwargs):
        collected.append(source_id)
        return original_collect(self, source_id, **kwargs)

    monkeypatch.setattr(SourceRegistry, "collect", observed_collect)

    assert run_briefing() == 0
    payload = json.loads(capsys.readouterr().out)
    assert collected == [
        "fixture_rss_blog",
        "fixture_atom_research",
        "fixture_github_repo",
        "fixture_exchange_docs",
        "fixture_paper_meta",
    ]
    assert payload["sources"]["fixture_exchange_docs"]["access_denied"] == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'app.sqlite3'}")
    with Session(engine) as session:
        exchange_document = session.scalar(
            select(Document).where(Document.source_id == "fixture_exchange_docs")
        )
        assert exchange_document is not None
        assert exchange_document.robots_allowed is False
        assert exchange_document.robots_rule_matched == "/private"
        assert exchange_document.robots_evaluated_at is not None
        assert "disallow rule" in exchange_document.access_decision_reason
        assert exchange_document.collection_status == "access_denied"

        exchange_version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == exchange_document.document_id
            )
        )
        assert exchange_version is not None
        assert exchange_version.robots_allowed is False
        assert exchange_version.robots_rule_matched == "/private"
        assert exchange_version.robots_evaluated_at is not None
        assert exchange_version.raw_location is None
        assert exchange_version.normalized_location is None
        assert exchange_version.metadata_json["robots_allowed"] is False
        assert not (
            tmp_path / "storage" / "raw" / exchange_version.document_version_id
        ).exists()
        assert not (
            tmp_path / "storage" / "normalized" / exchange_version.document_version_id
        ).exists()

        atom_document = session.scalar(
            select(Document).where(Document.source_id == "fixture_atom_research")
        )
        assert atom_document is not None
        assert atom_document.robots_allowed is True
        assert atom_document.access_status == "allowed"


def test_run_briefing_rejects_source_target_outside_registry_allowlist(
    monkeypatch, tmp_path: Path
) -> None:
    bad_source = SourceRecord(
        {
            "source_id": "unlisted_active_source",
            "name": "Unlisted active source",
            "type": "fixture",
            "base_url": "fixture://unlisted_active_source",
            "connector_type": "rss",
            "active": True,
            "access_policy": {"allowlist": ["fixture://unlisted_active_source"]},
            "rate_limit": {"requests_per_minute": 60, "burst": 1},
            "metadata": {},
            "original_url": "https://unlisted.invalid/document",
            "license_notes": "fixture",
            "trust_tier": "tier_3",
            "cursor": None,
            "schedule": "fixture",
        }
    )
    collected: list[str] = []

    monkeypatch.setattr(
        SourceRegistry,
        "active_sources",
        property(lambda _registry: (bad_source,)),
    )

    def unexpected_collect(_registry, source_id: str, **_kwargs):
        collected.append(source_id)
        raise AssertionError("allowlist rejection happened after collection")

    monkeypatch.setattr(SourceRegistry, "collect", unexpected_collect)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.sqlite3'}")

    with pytest.raises(HostNotAllowedError, match="Source Registry allowlist"):
        run_briefing()
    assert collected == []
