"""Document registry and append-only document-version models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JsonObject, TimestampMixin, new_id, utc_now


COLLECTION_STATUSES = ("discovered", "collected", "failed", "access_denied")
PROCESSING_STATUSES = (
    "discovered",
    "collected",
    "normalized",
    "deduplicated",
    "classified",
    "extracted",
    "validated",
    "needs_review",
    "approved",
    "rejected",
    "archived",
    "duplicate",
    "irrelevant",
    "background_only",
    "access_denied",
    "failed",
)
ACCESS_STATUSES = ("allowed", "denied", "unknown")


class Document(TimestampMixin, Base):
    """Stable identity for one canonical URL.

    Content is never stored on this row.  New content creates a
    :class:`DocumentVersion` instead of overwriting an earlier version.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "canonical_url", name="uq_documents_source_canonical_url"
        ),
    )

    document_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    source_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("sources.source_id"), nullable=False, index=True
    )
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author_or_org: Mapped[str | None] = mapped_column(String(512))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(32))
    document_type: Mapped[str | None] = mapped_column(String(100))

    robots_allowed: Mapped[bool | str] = mapped_column(
        JSON, default="unknown", nullable=False
    )
    robots_rule_matched: Mapped[str | None] = mapped_column(String(2048))
    robots_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_decision_reason: Mapped[str | None] = mapped_column(Text)
    collection_status: Mapped[str] = mapped_column(
        String(32), default="discovered", nullable=False
    )
    processing_status: Mapped[str] = mapped_column(
        String(32), default="discovered", nullable=False
    )
    access_status: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False
    )
    license: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    source_version_ref: Mapped[str | None] = mapped_column(String(512))
    first_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id", "canonical_url", name="uq_documents_source_canonical_url"
        ),
        CheckConstraint(
            "collection_status IN ('discovered', 'collected', 'failed', 'access_denied')",
            name="ck_documents_collection_status",
        ),
        CheckConstraint(
            "processing_status IN ('discovered', 'collected', 'normalized', 'deduplicated', 'classified', 'extracted', 'validated', 'needs_review', 'approved', 'rejected', 'archived', 'duplicate', 'irrelevant', 'background_only', 'access_denied', 'failed')",
            name="ck_documents_processing_status",
        ),
        CheckConstraint(
            "access_status IN ('allowed', 'denied', 'unknown')",
            name="ck_documents_access_status",
        ),
    )

    source: Mapped["Source"] = relationship("Source", back_populates="documents")
    versions: Mapped[list["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentVersion.version_no",
    )


class DocumentVersion(TimestampMixin, Base):
    """Immutable snapshot metadata for one collected document body."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "content_hash", name="uq_document_versions_document_hash"
        ),
        CheckConstraint(
            "collection_status IN ('discovered', 'collected', 'failed', 'access_denied')",
            name="ck_document_versions_collection_status",
        ),
        CheckConstraint(
            "processing_status IN ('discovered', 'collected', 'normalized', 'deduplicated', 'classified', 'extracted', 'validated', 'needs_review', 'approved', 'rejected', 'archived', 'duplicate', 'irrelevant', 'background_only', 'access_denied', 'failed')",
            name="ck_document_versions_processing_status",
        ),
        CheckConstraint(
            "access_status IN ('allowed', 'denied', 'unknown')",
            name="ck_document_versions_access_status",
        ),
    )

    document_version_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    document_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("documents.document_id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    body_hash: Mapped[str | None] = mapped_column(String(128))
    source_version_ref: Mapped[str | None] = mapped_column(String(512))
    raw_location: Mapped[str | None] = mapped_column(Text)
    normalized_location: Mapped[str | None] = mapped_column(Text)
    change_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    collection_status: Mapped[str] = mapped_column(
        String(32), default="collected", nullable=False
    )
    processing_status: Mapped[str] = mapped_column(
        String(32), default="collected", nullable=False
    )
    access_status: Mapped[str] = mapped_column(
        String(16), default="unknown", nullable=False
    )
    license: Mapped[str | None] = mapped_column(Text)
    robots_allowed: Mapped[bool | str] = mapped_column(
        JSON, default="unknown", nullable=False
    )
    robots_rule_matched: Mapped[str | None] = mapped_column(String(2048))
    robots_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_decision_reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    document: Mapped["Document"] = relationship(
        "Document", back_populates="versions"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="document_version"
    )


__all__ = [
    "ACCESS_STATUSES",
    "COLLECTION_STATUSES",
    "Document",
    "DocumentVersion",
    "PROCESSING_STATUSES",
]
