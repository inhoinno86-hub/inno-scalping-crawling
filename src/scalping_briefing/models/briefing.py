"""Briefing archive models and the publish-time Evidence gate."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy import event
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .base import (
    Base,
    JsonArray,
    JsonObject,
    TimestampMixin,
    briefing_item_evidence,
    new_id,
    utc_now,
)


class EvidenceValidationError(ValueError):
    """Raised when a core briefing item has no bounded Evidence."""


def validate_briefing_item_evidence(item: "BriefingItem") -> "BriefingItem":
    """Validate the persistent Evidence contract for one briefing item.

    Every core claim needs at least one Evidence row.  Two bounded quotes is
    the maximum accepted by the publishable JSON contract.  The function is
    intentionally side-effect free and can be used before a session flush.
    """

    # Python-side column defaults are applied at flush time, so an unsaved
    # defaulted item has ``core_claim is None``.  Treat that as the contract's
    # default ``True`` rather than accidentally skipping validation.
    if item.core_claim is False:
        return item

    evidence = list(item.evidence or ())
    if not evidence:
        raise EvidenceValidationError(
            f"briefing item {item.briefing_item_id!r} requires at least one Evidence"
        )
    if len(evidence) > 2:
        raise EvidenceValidationError(
            f"briefing item {item.briefing_item_id!r} has more than two Evidence rows"
        )
    for record in evidence:
        if not record.document_version_id and record.document_version is None:
            raise EvidenceValidationError(
                f"evidence {record.evidence_id!r} requires document_version_id"
            )
        if not record.quote or len(record.quote) > 300:
            raise EvidenceValidationError(
                f"evidence {record.evidence_id!r} quote must contain 1..300 characters"
            )
    return item


class Briefing(TimestampMixin, Base):
    """One scheduled or manually triggered briefing archive record."""

    __tablename__ = "briefings"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('scheduled', 'manual')",
            name="ck_briefings_trigger_type",
        ),
        CheckConstraint(
            "run_attempt >= 1", name="ck_briefings_run_attempt"
        ),
        CheckConstraint(
            "run_status IN ('pending', 'running', 'success', 'failed')",
            name="ck_briefings_run_status",
        ),
        CheckConstraint(
            "publication_status IN ('draft', 'pending_approval', 'approved', 'rejected', 'published', 'archived')",
            name="ck_briefings_publication_status",
        ),
    )

    briefing_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    trigger_type: Mapped[str] = mapped_column(
        String(32), default="manual", nullable=False
    )
    run_attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    window_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    run_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    publication_status: Mapped[str] = mapped_column(
        String(32), default="draft", nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    shared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Seoul", nullable=False
    )
    markdown_location: Mapped[str | None] = mapped_column(Text)
    source_summary: Mapped[JsonObject] = mapped_column(
        JSON, default=dict, nullable=False
    )
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    approved_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_truncated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    items: Mapped[list["BriefingItem"]] = relationship(
        "BriefingItem",
        back_populates="briefing",
        cascade="all, delete-orphan",
        order_by="BriefingItem.rank",
    )
    deliveries: Mapped[list["Delivery"]] = relationship(
        "Delivery",
        back_populates="briefing",
        cascade="all, delete-orphan",
        order_by="Delivery.attempted_at",
    )


class BriefingItem(TimestampMixin, Base):
    """Ranked briefing claim with persistent Evidence associations."""

    __tablename__ = "briefing_items"
    __table_args__ = (
        CheckConstraint(
            "strategy_candidate_id IS NOT NULL OR strategy_id IS NOT NULL",
            name="ck_briefing_items_strategy_target",
        ),
        CheckConstraint("rank >= 1", name="ck_briefing_items_rank"),
    )

    briefing_item_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    briefing_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("briefings.briefing_id"), nullable=False, index=True
    )
    strategy_candidate_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("strategy_candidates.candidate_id"), index=True
    )
    # Approved Strategy records are a later phase, so strategy_id remains a
    # portable opaque identifier rather than an FK to a non-existent table.
    strategy_id: Mapped[str | None] = mapped_column(String(255), index=True)
    reason_included: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    carried_over: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    core_claim: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    canonical_name: Mapped[str | None] = mapped_column(String(512))
    summary: Mapped[str | None] = mapped_column(Text)
    asset_classes: Mapped[JsonArray] = mapped_column(JSON, default=list, nullable=False)
    strategy_families: Mapped[JsonArray] = mapped_column(
        JSON, default=list, nullable=False
    )
    holding_horizon: Mapped[str | None] = mapped_column(String(128))
    value_score: Mapped[float | None] = mapped_column()

    briefing: Mapped["Briefing"] = relationship("Briefing", back_populates="items")
    strategy_candidate: Mapped["StrategyCandidate | None"] = relationship(
        "StrategyCandidate", back_populates="briefing_items"
    )
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence",
        secondary=briefing_item_evidence,
        back_populates="briefing_items",
    )

    @property
    def has_evidence(self) -> bool:
        """Whether this item currently has at least one Evidence association."""

        return bool(self.evidence)

    def validate_evidence(self) -> "BriefingItem":
        """Validate and return this item for fluent pre-publish checks."""

        return validate_briefing_item_evidence(self)


@event.listens_for(Session, "before_flush")
def _enforce_briefing_item_evidence(
    session: Session, _flush_context: Any, _instances: Any
) -> None:
    """Reject persisted core items that have no Evidence association."""

    candidates = set(session.new).union(session.dirty)
    for item in candidates:
        if isinstance(item, BriefingItem):
            validate_briefing_item_evidence(item)


__all__ = [
    "Briefing",
    "BriefingItem",
    "EvidenceValidationError",
    "validate_briefing_item_evidence",
]
