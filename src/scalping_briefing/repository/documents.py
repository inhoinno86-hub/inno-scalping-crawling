"""Append-only persistence for collected documents and their versions.

The repository owns only the collection boundary.  It canonicalizes URLs,
keeps one stable :class:`Document` per source URL, and appends immutable
``DocumentVersion`` rows when content changes.  Body bytes are written through
the existing local storage boundary and are never written when access is not
explicitly allowed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from os import PathLike
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from scalping_briefing.models import Document, DocumentVersion
from scalping_briefing.models.base import new_id, utc_now
from scalping_briefing.normalize.urls import normalize_url
from scalping_briefing.pipeline import state_machine
from scalping_briefing.storage.files import DEFAULT_STORAGE_ROOT, LocalFileStorage


Body = str | bytes | bytearray | memoryview


@dataclass(slots=True)
class IngestionResult:
    """Result of one repository ingestion attempt.

    ``document_version`` points at the existing version for a duplicate and
    at the newly appended version otherwise.  ``created`` distinguishes those
    cases without requiring callers to inspect database row counts.
    """

    document: Document
    document_version: DocumentVersion | None
    created: bool
    duplicate: bool = False
    access_denied: bool = False

    @property
    def version(self) -> DocumentVersion | None:
        """Compatibility alias for callers that use the shorter name."""

        return self.document_version

    @property
    def version_created(self) -> bool:
        """Return whether this call appended a new version row."""

        return self.created


DocumentIngestionResult = IngestionResult


class DocumentRepository:
    """Persist documents while retaining every collected content version."""

    def __init__(
        self,
        session: Session,
        storage: LocalFileStorage | None = None,
        *,
        storage_root: str | PathLike[str] | None = None,
        storage_config: Any | None = None,
    ) -> None:
        if storage is not None and storage_root is not None:
            raise TypeError("use storage or storage_root, not both")
        self.session = session
        self._storage = storage
        self._storage_root = storage_root or DEFAULT_STORAGE_ROOT
        self._storage_config = storage_config

    @property
    def storage(self) -> LocalFileStorage | None:
        """Return explicitly supplied storage, if it has not been needed yet."""

        return self._storage

    def _get_storage(self) -> LocalFileStorage:
        if self._storage is None:
            self._storage = LocalFileStorage(
                self._storage_root,
                config=self._storage_config,
            )
        return self._storage

    @staticmethod
    def _as_bytes(value: Body) -> bytes:
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, (bytes, bytearray, memoryview)):
            return bytes(value)
        raise TypeError("document body must be text or bytes-like")

    @classmethod
    def _hash_body(cls, value: Body) -> str:
        """Return a stable SHA-256 identifier for supplied body content."""

        return f"sha256:{sha256(cls._as_bytes(value)).hexdigest()}"

    @staticmethod
    def _require_hash(name: str, value: str | None) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        return value

    @staticmethod
    def _transition(
        record: Document | DocumentVersion,
        field_name: str,
        target: str,
    ) -> None:
        """Validate one state change before assigning its persisted value."""

        current = getattr(record, field_name)
        if current == target:
            return
        state_machine.transition(current, target)
        setattr(record, field_name, target)

    @staticmethod
    def _metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("metadata must be a mapping")
        return dict(value)

    def _find_document(self, source_id: str, canonical_url: str) -> Document | None:
        return self.session.scalar(
            select(Document).where(
                Document.source_id == source_id,
                Document.canonical_url == canonical_url,
            )
        )

    def _versions(self, document_id: str) -> list[DocumentVersion]:
        return list(
            self.session.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no)
            )
        )

    @staticmethod
    def _same_content(
        version: DocumentVersion,
        content_hash: str,
        body_hash: str | None,
    ) -> bool:
        if version.content_hash != content_hash:
            return False
        # A missing body hash means the caller did not provide a raw body.  In
        # that case content_hash remains the authoritative deduplication key.
        if body_hash is None or version.body_hash is None:
            return True
        return version.body_hash == body_hash

    @staticmethod
    def _next_version_no(versions: list[DocumentVersion]) -> int:
        return max((version.version_no for version in versions), default=0) + 1

    @staticmethod
    def _change_summary(
        previous: DocumentVersion | None,
        content_hash: str,
        body_hash: str | None,
        source_version_ref: str | None,
        supplied: str | None,
    ) -> str:
        if supplied and supplied.strip():
            return supplied.strip()
        if previous is None:
            return "Initial collected document version."

        changes: list[str] = []
        if previous.content_hash != content_hash:
            changes.append("content hash changed")
        if previous.body_hash != body_hash:
            changes.append("body hash changed")
        if previous.source_version_ref != source_version_ref:
            changes.append("source version reference changed")
        if not changes:
            changes.append("content metadata changed")
        return "; ".join(changes).capitalize() + "."

    @staticmethod
    def _denied_hash(source_id: str, canonical_url: str) -> str:
        return DocumentRepository._hash_body(
            f"access-denied:{source_id}:{canonical_url}"
        )

    @staticmethod
    def _default_decision_reason(robots_allowed: bool | str) -> str:
        return f"robots access was not explicitly allowed ({robots_allowed!r})"

    def _apply_document_observation(
        self,
        document: Document,
        *,
        original_url: str,
        title: str,
        author_or_org: str | None,
        published_at: datetime | None,
        language: str | None,
        document_type: str | None,
        license: str | None,
        robots_allowed: bool | str,
        robots_rule_matched: str | None,
        robots_evaluated_at: datetime | None,
        access_decision_reason: str,
        source_version_ref: str | None,
        metadata: Mapping[str, Any] | None,
        content_hash: str,
        retrieved_at: datetime,
        allowed: bool,
    ) -> None:
        if document.original_url is None:
            document.original_url = original_url
        if not document.title and title:
            document.title = title
        if author_or_org is not None:
            document.author_or_org = author_or_org
        if published_at is not None:
            document.published_at = published_at
        if language is not None:
            document.language = language
        if document_type is not None:
            document.document_type = document_type
        if license is not None:
            document.license = license

        document.robots_allowed = robots_allowed
        document.robots_rule_matched = robots_rule_matched
        document.robots_evaluated_at = robots_evaluated_at
        document.access_decision_reason = access_decision_reason
        document.source_version_ref = source_version_ref
        document.last_checked_at = retrieved_at
        if document.first_collected_at is None and allowed:
            document.first_collected_at = retrieved_at
        document.access_status = "allowed" if allowed else "denied"
        document.content_hash = content_hash

        if metadata is not None:
            merged = dict(document.metadata_json or {})
            merged.update(metadata)
            document.metadata_json = merged

    def ingest_document(
        self,
        source_id: str | None = None,
        url: str | None = None,
        *,
        source: Any | None = None,
        original_url: str | None = None,
        canonical_url: str | None = None,
        title: str | None = None,
        author_or_org: str | None = None,
        published_at: datetime | None = None,
        language: str | None = None,
        document_type: str | None = None,
        license: str | None = None,
        source_version_ref: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        content: Body | None = None,
        body: Body | None = None,
        raw_body: Body | None = None,
        normalized_body: Body | None = None,
        content_hash: str | None = None,
        body_hash: str | None = None,
        change_summary: str | None = None,
        robots_allowed: bool | str = "unknown",
        robots_rule_matched: str | None = None,
        robots_evaluated_at: datetime | None = None,
        access_decision_reason: str | None = None,
        retrieved_at: datetime | None = None,
        document_id: str | None = None,
    ) -> IngestionResult:
        """Ingest one document observation and return its persistence result.

        ``robots_allowed is True`` is the only value that permits body
        persistence.  ``content`` is treated as normalized content; ``body``
        is a convenient spelling for a body supplied to both storage layers.
        Explicit hashes are retained as supplied, while omitted hashes are
        derived in memory from the corresponding body values.
        """

        if source is not None:
            source_id = source_id or getattr(source, "source_id", None)
        if not source_id:
            raise ValueError("source_id is required")
        supplied_url = original_url or url or canonical_url
        if not supplied_url:
            raise ValueError("url is required")
        if body is not None:
            if raw_body is not None or normalized_body is not None:
                raise TypeError("body cannot be combined with raw_body or normalized_body")
            raw_body = body
            normalized_body = body
        if content is not None:
            if normalized_body is not None:
                raise TypeError("content cannot be combined with normalized_body")
            normalized_body = content

        canonical = normalize_url(supplied_url)
        title_value = title or canonical
        retrieved = retrieved_at or utc_now()
        allowed = robots_allowed is True
        evaluated_at = robots_evaluated_at
        if not allowed:
            evaluated_at = evaluated_at or retrieved
        decision_reason = access_decision_reason
        if not decision_reason:
            decision_reason = (
                "robots access explicitly allowed"
                if allowed
                else self._default_decision_reason(robots_allowed)
            )
        rule = robots_rule_matched
        if not allowed and not rule:
            rule = "no allow rule"

        if content_hash is None:
            if normalized_body is not None:
                content_hash = self._hash_body(normalized_body)
            elif raw_body is not None:
                content_hash = self._hash_body(raw_body)
            elif not allowed:
                content_hash = self._denied_hash(source_id, canonical)
            else:
                raise ValueError(
                    "content_hash or body content is required for allowed ingestion"
                )
        content_hash = self._require_hash("content_hash", content_hash)
        if body_hash is None and raw_body is not None:
            body_hash = self._hash_body(raw_body)
        body_hash = (
            self._require_hash("body_hash", body_hash) if body_hash is not None else None
        )

        document = self._find_document(source_id, canonical)
        if document is None:
            document = Document(
                document_id=document_id or new_id(),
                source_id=source_id,
                canonical_url=canonical,
                original_url=supplied_url,
                title=title_value,
                author_or_org=author_or_org,
                published_at=published_at,
                language=language,
                document_type=document_type,
                robots_allowed=robots_allowed,
                robots_rule_matched=rule,
                robots_evaluated_at=evaluated_at,
                access_decision_reason=decision_reason,
                collection_status="discovered",
                processing_status="discovered",
                access_status="unknown",
                license=license,
                content_hash=None,
                source_version_ref=None,
                metadata=self._metadata(metadata),
            )
            self.session.add(document)
            self._transition(document, "collection_status", "collected" if allowed else "access_denied")
            self._transition(document, "processing_status", "collected" if allowed else "access_denied")
        else:
            target = "collected" if allowed else "access_denied"
            self._transition(document, "collection_status", target)
            self._transition(document, "processing_status", target)

        self._apply_document_observation(
            document,
            original_url=supplied_url,
            title=title_value,
            author_or_org=author_or_org,
            published_at=published_at,
            language=language,
            document_type=document_type,
            license=license,
            robots_allowed=robots_allowed,
            robots_rule_matched=rule,
            robots_evaluated_at=evaluated_at,
            access_decision_reason=decision_reason,
            source_version_ref=source_version_ref,
            metadata=metadata,
            content_hash=content_hash,
            retrieved_at=retrieved,
            allowed=allowed,
        )

        versions = self._versions(document.document_id)
        duplicate_version = next(
            (
                version
                for version in versions
                if self._same_content(version, content_hash, body_hash)
            ),
            None,
        )
        if duplicate_version is None:
            # The schema keeps content_hash unique per document.  If only the
            # raw representation differs, retain the existing normalized
            # content version rather than attempting an impossible duplicate.
            duplicate_version = next(
                (version for version in versions if version.content_hash == content_hash),
                None,
            )
        if duplicate_version is not None:
            self.session.flush()
            return IngestionResult(
                document=document,
                document_version=duplicate_version,
                created=False,
                duplicate=True,
                access_denied=not allowed,
            )

        previous = versions[-1] if versions else None
        version = DocumentVersion(
            document_version_id=new_id(),
            document_id=document.document_id,
            version_no=self._next_version_no(versions),
            retrieved_at=retrieved,
            content_hash=content_hash,
            body_hash=body_hash,
            source_version_ref=source_version_ref,
            raw_location=None,
            normalized_location=None,
            change_summary=self._change_summary(
                previous,
                content_hash,
                body_hash,
                source_version_ref,
                change_summary,
            ),
            collection_status="discovered",
            processing_status="discovered",
            access_status="allowed" if allowed else "denied",
            license=license,
            robots_allowed=robots_allowed,
            robots_rule_matched=rule,
            robots_evaluated_at=evaluated_at,
            access_decision_reason=decision_reason,
            metadata=self._metadata(metadata),
        )
        target = "collected" if allowed else "access_denied"
        self._transition(version, "collection_status", target)
        self._transition(version, "processing_status", target)

        # Access-denied records retain metadata and hashes only.  In
        # particular, this branch never initializes local body storage.
        if allowed:
            storage = self._get_storage()
            if raw_body is not None:
                version.raw_location = str(
                    storage.write_raw(version.document_version_id, raw_body)
                )
            if normalized_body is not None:
                version.normalized_location = str(
                    storage.write_normalized(
                        version.document_version_id,
                        normalized_body,
                    )
                )

        self.session.add(version)
        self.session.flush()
        return IngestionResult(
            document=document,
            document_version=version,
            created=True,
            duplicate=False,
            access_denied=not allowed,
        )

    def ingest(self, *args: Any, **kwargs: Any) -> IngestionResult:
        """Short alias for :meth:`ingest_document`."""

        return self.ingest_document(*args, **kwargs)

    def get_document(self, source_id: str, url: str) -> Document | None:
        """Fetch a document by source and canonicalized URL."""

        return self._find_document(source_id, normalize_url(url))

    def list_versions(self, document: Document | str) -> list[DocumentVersion]:
        """Return all retained versions in ascending version order."""

        document_id = document.document_id if isinstance(document, Document) else document
        return self._versions(document_id)

    def get_versions(self, document: Document | str) -> list[DocumentVersion]:
        """Compatibility alias for :meth:`list_versions`."""

        return self.list_versions(document)


DocumentVersionRepository = DocumentRepository


__all__ = [
    "DocumentIngestionResult",
    "DocumentRepository",
    "DocumentVersionRepository",
    "IngestionResult",
]
