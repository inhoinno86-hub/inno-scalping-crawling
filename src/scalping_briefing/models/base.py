"""Shared SQLAlchemy 2.0 declarations for the persistence layer.

The model layer deliberately uses portable SQL types.  JSON columns are used
for small structured values (lists, policy objects, and metadata) instead of
PostgreSQL-only ARRAY/JSONB types so the default SQLite database remains a
first-class test and development backend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeAlias
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


JsonObject: TypeAlias = dict[str, Any]
JsonArray: TypeAlias = list[Any]


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for Python-side defaults."""

    return datetime.now(UTC)


def new_id() -> str:
    """Return a string identifier without requiring a database-specific UUID."""

    return str(uuid4())


class Base(DeclarativeBase):
    """Declarative base shared by every persistence model."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(column_0_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )

    def __init__(self, **kwargs: Any) -> None:
        """Initialize mapped attributes and accept schema ``metadata`` keys.

        SQLAlchemy reserves the class-level name ``metadata`` for
        ``Base.metadata``.  The database column is therefore exposed as
        ``metadata_json`` in Python while this constructor still accepts the
        JSON-schema field name ``metadata``.
        """

        if "metadata" in kwargs and "metadata_json" in type(self).__mapper__.attrs:
            kwargs["metadata_json"] = kwargs.pop("metadata")

        mapper_attributes = set(type(self).__mapper__.attrs.keys())
        unknown = set(kwargs) - mapper_attributes
        if unknown:
            names = ", ".join(sorted(unknown))
            raise TypeError(f"unexpected mapped attribute(s): {names}")
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattribute__(self, name: str) -> Any:
        """Expose JSON-schema ``metadata`` on instances without shadowing Base.metadata."""

        if name == "metadata":
            mapper = getattr(type(self), "__mapper__", None)
            if mapper is not None and "metadata_json" in mapper.attrs:
                return object.__getattribute__(self, "metadata_json")
        return super().__getattribute__(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Route instance ``metadata`` assignments to its mapped JSON column."""

        if name == "metadata":
            mapper = getattr(type(self), "__mapper__", None)
            if mapper is not None and "metadata_json" in mapper.attrs:
                name = "metadata_json"
        super().__setattr__(name, value)


class TimestampMixin:
    """Created/updated timestamps shared by mutable registry records."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


briefing_item_evidence = Table(
    "briefing_item_evidence",
    Base.metadata,
    Column(
        "briefing_item_id",
        String(255),
        ForeignKey("briefing_items.briefing_item_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "evidence_id",
        String(255),
        ForeignKey("evidence.evidence_id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


__all__ = [
    "Base",
    "JsonArray",
    "JsonObject",
    "TimestampMixin",
    "briefing_item_evidence",
    "new_id",
    "utc_now",
]
