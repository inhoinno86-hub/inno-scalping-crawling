"""Source registry model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JsonObject, TimestampMixin, new_id


class Source(TimestampMixin, Base):
    """A configured public source and its access-policy audit metadata."""

    __tablename__ = "sources"

    source_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(default=False, nullable=False)

    access_policy: Mapped[JsonObject] = mapped_column(
        JSON, default=dict, nullable=False
    )
    robots_allowed: Mapped[bool | str] = mapped_column(
        JSON, default="unknown", nullable=False
    )
    robots_rule_matched: Mapped[str | None] = mapped_column(String(2048))
    robots_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # §8.1 calls this timestamp ``robots_checked_at``.  Keep both source-level
    # names because the JSON contracts use ``robots_evaluated_at``.
    robots_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    access_decision_reason: Mapped[str | None] = mapped_column(Text)

    terms_reference: Mapped[str | None] = mapped_column(String(2048))
    license_notes: Mapped[str | None] = mapped_column(Text)
    rate_limit: Mapped[JsonObject] = mapped_column(JSON, default=dict, nullable=False)
    schedule: Mapped[list[Any] | str | None] = mapped_column(JSON)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cursor: Mapped[str | None] = mapped_column(Text)
    trust_tier: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    error_state: Mapped[JsonObject | None] = mapped_column(JSON)
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="source"
    )
    collection_jobs: Mapped[list["CollectionJob"]] = relationship(
        "CollectionJob", back_populates="source"
    )


__all__ = ["Source"]
