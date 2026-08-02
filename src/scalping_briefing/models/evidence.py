"""Evidence records and their document-version traceability link."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JsonObject, TimestampMixin, briefing_item_evidence, new_id, utc_now


class Evidence(TimestampMixin, Base):
    """Bounded quote supporting one extracted strategy field.

    ``document_version_id`` is mandatory by design.  Evidence cannot point
    only at a mutable document identity because public claims must remain
    reproducible after a source changes.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            "length(quote) BETWEEN 1 AND 300", name="ck_evidence_quote_length"
        ),
    )

    evidence_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    document_version_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("document_versions.document_version_id"),
        nullable=False,
        index=True,
    )
    strategy_candidate_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("strategy_candidates.candidate_id"),
        nullable=False,
        index=True,
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    section_or_locator: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(2048))
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    document_version: Mapped["DocumentVersion"] = relationship(
        "DocumentVersion", back_populates="evidence"
    )
    strategy_candidate: Mapped["StrategyCandidate"] = relationship(
        "StrategyCandidate", back_populates="evidence"
    )
    briefing_items: Mapped[list["BriefingItem"]] = relationship(
        "BriefingItem",
        secondary=briefing_item_evidence,
        back_populates="evidence",
    )


__all__ = ["Evidence"]
