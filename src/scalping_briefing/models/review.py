"""Human review history for strategy candidates."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, new_id, utc_now


class Review(TimestampMixin, Base):
    """Append-only reviewer decision record."""

    __tablename__ = "reviews"

    review_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    strategy_candidate_id: Mapped[str] = mapped_column(
        String(255),
        ForeignKey("strategy_candidates.candidate_id"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    strategy_candidate: Mapped["StrategyCandidate"] = relationship(
        "StrategyCandidate", back_populates="reviews"
    )


__all__ = ["Review"]
