"""Persistent delivery attempt contract and idempotency constraint."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from scalping_briefing.delivery.guard import (
    make_idempotency_key,
    validate_idempotency_key,
)

from .base import Base, TimestampMixin, new_id, utc_now


class Delivery(TimestampMixin, Base):
    """One idempotent delivery record.

    The unique key is deliberately on the full idempotency key.  A resend
    approval is a policy decision returned by :mod:`delivery.guard`; this row
    remains the durable deduplication boundary and no provider call is made
    here.
    """

    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_deliveries_idempotency_key"),
        CheckConstraint(
            "attempt_no >= 1", name="ck_deliveries_attempt_no"
        ),
        CheckConstraint(
            "status IN ('pending', 'success', 'failed', 'rejected')",
            name="ck_deliveries_status",
        ),
        # SQL LIKE is supported by both SQLite and PostgreSQL.  The check
        # enforces exactly three non-empty components without regex features.
        CheckConstraint(
            "idempotency_key NOT LIKE ':%' "
            "AND idempotency_key NOT LIKE '%:' "
            "AND idempotency_key NOT LIKE '%::%' "
            "AND idempotency_key NOT LIKE '%:%:%:%' "
            "AND idempotency_key LIKE '%:%:%' "
            "AND idempotency_key NOT LIKE '% %'",
            name="ck_deliveries_idempotency_key_shape",
        ),
        CheckConstraint(
            "attempt_no < 2 OR (resend_reason IS NOT NULL AND trim(resend_reason) <> '' AND resend_approved_by IS NOT NULL AND trim(resend_approved_by) <> '')",
            name="ck_deliveries_resend_approval",
        ),
    )

    delivery_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    briefing_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("briefings.briefing_id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(128))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    resend_reason: Mapped[str | None] = mapped_column(Text)
    resend_approved_by: Mapped[str | None] = mapped_column(String(255))
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    provider_reference: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)

    briefing: Mapped["Briefing"] = relationship("Briefing", back_populates="deliveries")

    @validates("idempotency_key")
    def _validate_key(self, _key: str, value: str) -> str:
        return validate_idempotency_key(value)

    @classmethod
    def for_briefing(
        cls,
        *,
        briefing_id: str,
        channel: str,
        content_hash: str,
        delivery_id: str | None = None,
        attempt_no: int = 1,
        resend_reason: str | None = None,
        resend_approved_by: str | None = None,
        status: str = "pending",
        attempted_at: datetime | None = None,
    ) -> "Delivery":
        """Construct a delivery with a correctly derived idempotency key."""

        return cls(
            delivery_id=delivery_id or new_id(),
            briefing_id=briefing_id,
            channel=channel,
            content_hash=content_hash,
            idempotency_key=make_idempotency_key(
                briefing_id, channel, content_hash
            ),
            attempt_no=attempt_no,
            resend_reason=resend_reason,
            resend_approved_by=resend_approved_by,
            status=status,
            attempted_at=attempted_at,
        )


__all__ = ["Delivery"]
