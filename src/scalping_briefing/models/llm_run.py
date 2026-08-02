"""Audit record for a bounded LLM extraction or briefing-draft run."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from .base import Base, JsonObject, TimestampMixin, new_id, utc_now


class LLMRun(TimestampMixin, Base):
    """Metadata needed to reproduce and audit one LLM boundary invocation."""

    __tablename__ = "llm_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_llm_runs_status",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_llm_runs_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_llm_runs_output_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_llm_runs_total_tokens",
        ),
    )

    llm_run_id: Mapped[str] = mapped_column(
        String(255), primary_key=True, default=new_id
    )
    run_type: Mapped[str] = mapped_column(
        String(64), default="extraction", nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model = synonym("model_name")
    prompt_version: Mapped[str] = mapped_column(String(255), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_document_version_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("document_versions.document_version_id"), index=True
    )
    input_hash: Mapped[str | None] = mapped_column(String(128))
    output_hash: Mapped[str | None] = mapped_column(String(128))
    input_location: Mapped[str | None] = mapped_column(Text)
    output_location: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JsonObject] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    input_document_version: Mapped["DocumentVersion | None"] = relationship(
        "DocumentVersion"
    )


__all__ = ["LLMRun"]
