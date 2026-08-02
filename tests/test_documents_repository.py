from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing.models import Base, DocumentVersion, Source
from scalping_briefing.pipeline.state_machine import InvalidTransition
from scalping_briefing.repository.documents import DocumentRepository
from scalping_briefing.storage.files import LocalFileStorage


@pytest.fixture
def repository(tmp_path: Path) -> tuple[DocumentRepository, Session, LocalFileStorage]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(
        Source(
            source_id="fixture-source",
            name="Fixture source",
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
    yield DocumentRepository(session, storage=storage), session, storage
    session.close()
    engine.dispose()


def test_repository_canonicalizes_url_and_deduplicates_content(
    repository: tuple[DocumentRepository, Session, LocalFileStorage],
) -> None:
    repo, session, storage = repository
    first = repo.ingest_document(
        source_id="fixture-source",
        url="HTTPS://EXAMPLE.INVALID:443/research/item?utm_source=fixture#part",
        title="Fixture item",
        body="same fixture body",
        robots_allowed=True,
    )
    second = repo.ingest_document(
        source_id="fixture-source",
        url="https://example.invalid/research/item",
        title="Fixture item",
        body="same fixture body",
        robots_allowed=True,
    )

    assert first.created is True
    assert second.created is False
    assert second.duplicate is True
    assert first.document.canonical_url == "https://example.invalid/research/item"
    versions = list(session.scalars(select(DocumentVersion)))
    assert len(versions) == 1
    assert Path(versions[0].raw_location).read_text() == "same fixture body"
    assert Path(versions[0].normalized_location).read_text() == "same fixture body"
    assert storage.raw_directory.joinpath(versions[0].document_version_id).is_file()


def test_repository_appends_changed_content_and_retains_prior_location(
    repository: tuple[DocumentRepository, Session, LocalFileStorage],
) -> None:
    repo, session, _storage = repository
    first = repo.ingest_document(
        source_id="fixture-source",
        url="https://example.invalid/research/item",
        body="fixture body v1",
        source_version_ref="fixture-v1",
        robots_allowed=True,
    )
    second = repo.ingest_document(
        source_id="fixture-source",
        url="https://example.invalid/research/item",
        body="fixture body v2",
        source_version_ref="fixture-v2",
        robots_allowed=True,
    )

    versions = list(
        session.scalars(
            select(DocumentVersion).order_by(DocumentVersion.version_no)
        )
    )
    assert second.created is True
    assert [version.version_no for version in versions] == [1, 2]
    assert versions[0].document_version_id == first.document_version.document_version_id
    assert versions[0].change_summary == "Initial collected document version."
    assert versions[1].change_summary
    assert versions[0].body_hash != versions[1].body_hash
    assert Path(versions[0].raw_location).read_text() == "fixture body v1"
    assert Path(versions[1].raw_location).read_text() == "fixture body v2"


def test_repository_denies_unknown_access_without_body_storage(
    repository: tuple[DocumentRepository, Session, LocalFileStorage],
) -> None:
    repo, session, storage = repository
    result = repo.ingest_document(
        source_id="fixture-source",
        url="https://example.invalid/private/item",
        title="Private fixture",
        raw_body="must not be written",
        normalized_body="must not be written",
        robots_allowed="unknown",
        robots_rule_matched="/private",
        access_decision_reason="robots evaluation did not allow access",
        metadata={"fixture": "disallowed"},
    )

    document = result.document
    version = result.document_version
    assert result.access_denied is True
    assert document.collection_status == "access_denied"
    assert document.processing_status == "access_denied"
    assert document.access_status == "denied"
    assert document.robots_rule_matched == "/private"
    assert document.robots_evaluated_at is not None
    assert document.access_decision_reason == "robots evaluation did not allow access"
    assert version is not None
    assert version.collection_status == "access_denied"
    assert version.processing_status == "access_denied"
    assert version.raw_location is None
    assert version.normalized_location is None
    assert list(storage.raw_directory.iterdir()) == []
    assert list(storage.normalized_directory.iterdir()) == []
    assert session.get(DocumentVersion, version.document_version_id) is version


def test_repository_rejects_invalid_existing_access_transition(
    repository: tuple[DocumentRepository, Session, LocalFileStorage],
) -> None:
    repo, _session, _storage = repository
    repo.ingest_document(
        source_id="fixture-source",
        url="https://example.invalid/research/item",
        body="allowed first",
        robots_allowed=True,
    )

    with pytest.raises(InvalidTransition):
        repo.ingest_document(
            source_id="fixture-source",
            url="https://example.invalid/research/item",
            robots_allowed=False,
            access_decision_reason="access no longer allowed",
        )
