from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from scalping_briefing.logging_setup import configure_logging
from scalping_briefing.models import Base, Document, DocumentVersion, Source
from scalping_briefing.normalize.sanitize import sanitize_html
from scalping_briefing.net.retry import CollectionFailedError, RetryPolicy
from scalping_briefing.repository.documents import DocumentRepository
from scalping_briefing.storage.files import LocalFileStorage


def _repository(tmp_path: Path) -> tuple[DocumentRepository, Session, LocalFileStorage]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        Source(
            source_id="phase1-fixture",
            name="Phase 1 fixture",
            type="research",
            base_url="https://example.invalid",
            connector_type="fixture",
        )
    )
    session.flush()
    storage = LocalFileStorage(
        tmp_path / "storage",
        settings={"raw_retention_days": 1, "normalized_retention_days": "unlimited"},
    )
    return DocumentRepository(session, storage=storage), session, storage


def test_phase1_dod1_reingest_creates_no_duplicate_version(tmp_path: Path) -> None:
    repo, session, _storage = _repository(tmp_path)
    first = repo.ingest_document(
        source_id="phase1-fixture",
        url="HTTPS://EXAMPLE.INVALID:443/article?utm_campaign=fixture#headline",
        raw_body="fixture-v1-raw",
        normalized_body="fixture-v1-normalized",
        robots_allowed=True,
    )
    second = repo.ingest_document(
        source_id="phase1-fixture",
        url="https://example.invalid/article",
        raw_body="fixture-v1-raw",
        normalized_body="fixture-v1-normalized",
        robots_allowed=True,
    )

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert session.scalar(select(func.count(DocumentVersion.document_version_id))) == 1
    document = session.scalar(select(Document))
    assert document is not None
    assert document.canonical_url == "https://example.invalid/article"


def test_phase1_dod2_changed_fixture_creates_new_version_with_change_summary(
    tmp_path: Path,
) -> None:
    repo, session, _storage = _repository(tmp_path)
    repo.ingest_document(
        source_id="phase1-fixture",
        url="https://example.invalid/article",
        raw_body="fixture-v1-raw",
        normalized_body="fixture-v1-normalized",
        source_version_ref="fixture-v1",
        robots_allowed=True,
    )
    second = repo.ingest_document(
        source_id="phase1-fixture",
        url="https://example.invalid/article",
        raw_body="fixture-v2-raw",
        normalized_body="fixture-v2-normalized",
        source_version_ref="fixture-v2",
        robots_allowed=True,
    )

    versions = list(
        session.scalars(
            select(DocumentVersion).order_by(DocumentVersion.version_no)
        )
    )
    assert second.created is True
    assert len(versions) == 2
    assert versions[1].change_summary
    assert versions[0].document_version_id != versions[1].document_version_id
    assert versions[0].body_hash != versions[1].body_hash
    assert Path(versions[0].raw_location).read_text() == "fixture-v1-raw"
    assert Path(versions[1].raw_location).read_text() == "fixture-v2-raw"


def test_phase1_dod3_robots_disallowed_ends_access_denied_without_body(
    tmp_path: Path,
) -> None:
    repo, session, storage = _repository(tmp_path)
    result = repo.ingest_document(
        source_id="phase1-fixture",
        url="https://example.invalid/private/article",
        raw_body="fixture body must not persist",
        normalized_body="normalized fixture must not persist",
        robots_allowed=False,
        robots_rule_matched="/private",
        access_decision_reason="robots rule disallowed this path",
    )

    document = result.document
    version = result.document_version
    assert version is not None
    assert document.collection_status == "access_denied"
    assert document.processing_status == "access_denied"
    assert version.collection_status == "access_denied"
    assert version.processing_status == "access_denied"
    assert document.robots_rule_matched == "/private"
    assert document.robots_evaluated_at is not None
    assert document.access_decision_reason == "robots rule disallowed this path"
    assert version.raw_location is None
    assert version.normalized_location is None
    assert list(storage.raw_directory.iterdir()) == []
    assert list(storage.normalized_directory.iterdir()) == []


def test_phase1_dod4_malicious_html_sanitized_and_injection_not_executed() -> None:
    source = Path(__file__).parent / "fixtures/sources/fixture_exchange_docs/response.html"

    sanitized = sanitize_html(source.read_text(encoding="utf-8"))

    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in sanitized
    assert "<script" not in sanitized.lower()
    assert "javascript:" not in sanitized.lower()
    assert "onerror" not in sanitized.lower()
    assert "alert('fixture script must never execute')" not in sanitized


def test_phase1_dod5_collection_failure_in_structured_log_and_alerts(
    tmp_path: Path,
) -> None:
    stream = StringIO()
    logger = logging.getLogger("test.phase1.collection-failure")
    configure_logging(stream=stream, logger=logger)
    policy = RetryPolicy(
        max_collect_retries=3,
        logger=logger,
        alerts_dir=tmp_path / "alerts",
    )

    def collect() -> None:
        raise RuntimeError("fixture collection failure")

    with pytest.raises(CollectionFailedError) as raised:
        policy.run(collect, sleeper=lambda _seconds: None, source_id="phase1-fixture")

    assert raised.value.state.terminal_error is True
    log_payload = json.loads(stream.getvalue().splitlines()[-1])
    assert log_payload["event"] == "collection_failure"
    assert log_payload["retry_count"] == 3
    alert_files = list((tmp_path / "alerts").glob("*.json"))
    assert len(alert_files) == 1
    alert_payload = json.loads(alert_files[0].read_text(encoding="utf-8"))
    assert alert_payload["event"] == "collection_failure"
    assert alert_payload["details"]["terminal_error"] is True
