"""Collection stage for the end-to-end briefing cycle.

The collection implementation stays at the orchestration boundary while the
source-specific policy, normalization, and persistence helpers remain owned by
``scalping_briefing.__init__``.  The legacy ``run_briefing`` entrypoint is kept
unchanged; this function only exposes its persisted document versions to the
cycle runner.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import scalping_briefing as briefing_package
from scalping_briefing.models import Base
from scalping_briefing.net.guards import RequestGuards
from scalping_briefing.net.rate_limit import SourceRateLimiter
from scalping_briefing.repository.documents import DocumentRepository
from scalping_briefing.sources.registry import SourceRegistry


@dataclass(slots=True)
class CollectionResult:
    """Persisted output and bounded counters from one collection stage."""

    document_versions: list[Any] = field(default_factory=list)
    source_summary: dict[str, Any] = field(default_factory=dict)
    collected_items: int = 0
    persisted_versions: int = 0
    duplicate_versions: int = 0
    access_denied: int = 0

    @property
    def versions(self) -> list[Any]:
        """Compatibility alias for callers using the shorter result name."""

        return self.document_versions

    @property
    def items(self) -> list[Any]:
        """Compatibility alias for the persisted document versions."""

        return self.document_versions

    def __iter__(self):
        return iter(self.document_versions)

    def __len__(self) -> int:
        return len(self.document_versions)

    def __getitem__(self, index: int) -> Any:
        return self.document_versions[index]

    def to_payload(self) -> dict[str, Any]:
        return {
            "document_versions": list(self.document_versions),
            "source_summary": dict(self.source_summary),
            "collected_items": self.collected_items,
            "persisted_versions": self.persisted_versions,
            "duplicate_versions": self.duplicate_versions,
            "access_denied": self.access_denied,
        }


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _source_value(source: Any, name: str, default: Any = None) -> Any:
    return briefing_package.source_value(source, name, default)


def _ensure_schema(session: Any) -> None:
    """Create the known tables when an injected session has an empty bind."""

    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        return
    bind = get_bind()
    if bind is not None:
        Base.metadata.create_all(bind)


def _persist_source(
    session: Any,
    repository: DocumentRepository,
    registry: SourceRegistry,
    source: Any,
    *,
    settings: Any,
    request_guards: RequestGuards,
    rate_limiter: SourceRateLimiter,
) -> tuple[list[Any], dict[str, Any]]:
    """Collect one source and return its versions plus bounded counters."""

    request_guards.validate_url(briefing_package._collection_target(source))
    result = registry.collect(source.source_id)
    robots_text = briefing_package._load_robots_text(
        registry,
        source,
        request_guards=request_guards,
        rate_limiter=rate_limiter,
        settings=settings,
    )

    versions: list[Any] = []
    created = 0
    duplicates = 0
    access_denied = 0
    for item in result.items:
        request_guards.validate_url(
            briefing_package._document_target(item, source)
        )
        rate_policy = _source_value(source, "rate_limit")
        if rate_policy is not None:
            rate_limiter.acquire_or_wait(source.source_id, rate_policy)
        decision = briefing_package._evaluate_document_robots(
            registry,
            source,
            item,
            robots_text,
        )
        original_url = briefing_package._item_url(item, source)
        existing = repository.get_document(source.source_id, original_url)
        if (
            not decision.allowed
            and existing is not None
            and existing.collection_status == "collected"
        ):
            duplicates += 1
            access_denied += 1
            continue

        ingestion = repository.ingest_document(
            source_id=source.source_id,
            original_url=original_url,
            title=str(item.get("title") or source.name),
            author_or_org=briefing_package._author_text(item.get("authors"))
            or source.name,
            published_at=briefing_package._parse_datetime(item.get("published_at")),
            language="en",
            document_type=str(
                _source_value(source, "type") or "source_document"
            ),
            license=briefing_package._license_text(item.get("license"))
            or str(_source_value(source, "license_notes", "")),
            source_version_ref=(
                item.get("source_version_ref")
                or result.metadata.get("commit_sha")
                or result.content_hash
            ),
            metadata=briefing_package._item_metadata(
                item,
                result,
                source,
                decision=decision,
            ),
            raw_body=(
                briefing_package._raw_body(item) if decision.allowed else None
            ),
            normalized_body=(
                briefing_package._normalized_body(item)
                if decision.allowed
                else None
            ),
            content_hash=briefing_package._content_hash(item),
            body_hash=briefing_package._body_hash(item),
            robots_allowed=decision.robots_allowed,
            robots_rule_matched=decision.robots_rule_matched,
            robots_evaluated_at=decision.robots_evaluated_at,
            access_decision_reason=decision.access_decision_reason,
        )
        if ingestion.created:
            created += 1
        if ingestion.duplicate:
            duplicates += 1
        if ingestion.access_denied:
            access_denied += 1
        version = ingestion.document_version
        if version is not None:
            versions.append(version)

    briefing_package._persist_source_cursor(session, source)
    return versions, {
        "items": len(result.items),
        "versions_created": created,
        "duplicates": duplicates,
        "access_denied": access_denied,
        "cursor": (
            dict(result.cursor) if result.cursor is not None else None
        ),
    }


def collect_documents(
    session: Any,
    *,
    settings: Any,
    registry: Any = None,
    storage_root: Path = Path("storage"),
) -> CollectionResult:
    """Collect active sources and return persisted ``DocumentVersion`` rows.

    ``session`` owns transaction boundaries.  A registry supplied by a caller
    is likewise left open; only the registry created by this function is
    closed here.  This keeps the function suitable for an injected in-memory
    session and for the normal fixture registry.
    """

    owned_registry = registry is None
    selected_registry = (
        registry if registry is not None else SourceRegistry(settings=settings)
    )
    try:
        _ensure_schema(session)
        briefing_package._sync_database_sources(session, selected_registry)
        repository = DocumentRepository(
            session,
            storage_root=storage_root,
            storage_config=settings,
        )
        request_guards = RequestGuards.from_source_registry(
            selected_registry,
            settings=settings,
        )
        rate_policies = {
            source.source_id: _source_value(source, "rate_limit")
            for source in selected_registry.active_sources
            if _source_value(source, "rate_limit") is not None
        }
        rate_limiter = SourceRateLimiter(rate_policies)

        versions: list[Any] = []
        seen_versions: set[str] = set()
        source_results: dict[str, dict[str, Any]] = {}
        collected_items = 0
        persisted_versions = 0
        duplicate_versions = 0
        access_denied = 0
        for source in selected_registry.active_sources:
            source_versions, source_result = _persist_source(
                session,
                repository,
                selected_registry,
                source,
                settings=settings,
                request_guards=request_guards,
                rate_limiter=rate_limiter,
            )
            source_results[source.source_id] = source_result
            collected_items += int(source_result["items"])
            persisted_versions += int(source_result["versions_created"])
            duplicate_versions += int(source_result["duplicates"])
            access_denied += int(source_result["access_denied"])
            for version in source_versions:
                identifier = str(
                    getattr(version, "document_version_id", id(version))
                )
                if identifier not in seen_versions:
                    seen_versions.add(identifier)
                    versions.append(version)

        session.flush()
        source_summary = {
            "total": len(source_results),
            "success": len(source_results),
            "failed": 0,
            "not_executed": 0,
            "sources": source_results,
        }
        info = getattr(session, "info", None)
        if isinstance(info, dict):
            info["source_summary"] = source_summary
        return CollectionResult(
            document_versions=versions,
            source_summary=source_summary,
            collected_items=collected_items,
            persisted_versions=persisted_versions,
            duplicate_versions=duplicate_versions,
            access_denied=access_denied,
        )
    finally:
        if owned_registry:
            close = getattr(selected_registry, "close", None)
            if callable(close):
                close()


__all__ = ["CollectionResult", "collect_documents"]
