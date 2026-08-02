"""Strategy candidate registry model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Float, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, JsonArray, JsonObject, TimestampMixin, new_id


FIELD_STATUS_VALUES = (
    "explicit",
    "inferred",
    "unknown",
    "conflicting",
    "not_applicable",
)


class StrategyCandidate(TimestampMixin, Base):
    """Structured, reviewable strategy extraction candidate.

    Core value fields and their ``*_status`` columns mirror the JSON Schema
    contract.  No strategy execution or backtest behavior is attached here.
    """

    __tablename__ = "strategy_candidates"
    __table_args__ = (
        CheckConstraint(
            "relevance_status IN ('relevant', 'irrelevant', 'background_only', 'unknown')",
            name="ck_strategy_candidates_relevance_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'needs_review', 'approved', 'rejected', 'archived')",
            name="ck_strategy_candidates_review_status",
        ),
        CheckConstraint(
            "source_confidence IS NULL OR (source_confidence >= 0 AND source_confidence <= 1)",
            name="ck_strategy_candidates_source_confidence",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR (extraction_confidence >= 0 AND extraction_confidence <= 1)",
            name="ck_strategy_candidates_extraction_confidence",
        ),
        CheckConstraint(
            "value_score IS NULL OR (value_score >= 0 AND value_score <= 100)",
            name="ck_strategy_candidates_value_score",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    strategy_id: Mapped[str | None] = mapped_column(String(255), index=True)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[JsonArray] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    asset_classes: Mapped[JsonArray] = mapped_column(JSON, default=list, nullable=False)
    market_types: Mapped[JsonArray] = mapped_column(JSON, default=list, nullable=False)
    strategy_families: Mapped[JsonArray] = mapped_column(
        JSON, default=list, nullable=False
    )
    holding_horizon: Mapped[str | None] = mapped_column(String(128))
    microstructure_level: Mapped[str | None] = mapped_column(String(128))
    tags: Mapped[JsonArray] = mapped_column(JSON, default=list, nullable=False)

    core_hypothesis: Mapped[str | None] = mapped_column(Text)
    core_hypothesis_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    signal_inputs: Mapped[JsonArray | None] = mapped_column(JSON)
    signal_inputs_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    entry_logic: Mapped[str | None] = mapped_column(Text)
    entry_logic_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    exit_logic: Mapped[str | None] = mapped_column(Text)
    exit_logic_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    required_data: Mapped[JsonArray | None] = mapped_column(JSON)
    required_data_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    required_frequency: Mapped[str | None] = mapped_column(String(128))
    risk_notes: Mapped[str | None] = mapped_column(Text)
    risk_notes_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    field_status: Mapped[JsonObject] = mapped_column(
        JSON, default=dict, nullable=False
    )

    relevance_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    review_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    source_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    value_score: Mapped[float | None] = mapped_column(Float)
    value_score_breakdown: Mapped[JsonObject] = mapped_column(
        JSON, default=dict, nullable=False
    )
    novelty_status: Mapped[str | None] = mapped_column(String(32))
    related_strategy_ids: Mapped[JsonArray] = mapped_column(
        JSON, default=list, nullable=False
    )
    document_version_ids: Mapped[JsonArray] = mapped_column(
        JSON, default=list, nullable=False
    )
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="strategy_candidate"
    )
    reviews: Mapped[list["Review"]] = relationship(
        "Review",
        back_populates="strategy_candidate",
        cascade="all, delete-orphan",
        order_by="Review.reviewed_at",
    )
    briefing_items: Mapped[list["BriefingItem"]] = relationship(
        "BriefingItem", back_populates="strategy_candidate"
    )


__all__ = ["FIELD_STATUS_VALUES", "StrategyCandidate"]
