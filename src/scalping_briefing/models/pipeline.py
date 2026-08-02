"""Database-backed collection job records.

Jobs hold scheduling and retry metadata only.  They do not execute a
connector; collection workers belong to a later phase.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from .base import Base, TimestampMixin, new_id


class CollectionJob(TimestampMixin, Base):
    """One source collection task and its independent retry metadata."""

    __tablename__ = "collection_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name="ck_collection_jobs_status",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_collection_jobs_attempt_no"),
        CheckConstraint("retry_count >= 0", name="ck_collection_jobs_retry_count"),
    )

    collection_job_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    # ``job_id`` is a convenient compatibility alias for task-oriented code;
    # the persisted column remains explicitly named collection_job_id.
    job_id = synonym("collection_job_id")
    source_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("sources.source_id"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(64), default="collect", nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    error_class: Mapped[str | None] = mapped_column(String(128))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_error: Mapped[bool] = mapped_column(default=False, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    source: Mapped["Source"] = relationship("Source", back_populates="collection_jobs")


__all__ = ["CollectionJob"]
