"""Phase 0 + Phase 1 offline-first briefing foundation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .config import (
    CONFIG_KEYS,
    Config,
    ConfigError,
    Settings,
    load_config,
    load_settings,
)
from .models import Base, Source
from .models.base import utc_now
from .net.guards import RequestGuards
from .net.rate_limit import SourceRateLimiter
from .net.robots import RobotsDecision, evaluate_robots
from .normalize.sanitize import sanitize_html
from .repository.documents import DocumentRepository
from .sources.registry import (
    SourceRegistry,
    SourceRecord,
    response_body,
    response_status,
    source_value,
)

__version__ = "0.1.0"


def run_briefing() -> int:
    """Collect active fixture sources and persist versions in dry-run mode.

    This is deliberately a collection vertical slice.  It does not create a
    briefing, call an LLM, or invoke a delivery provider.
    """

    settings = load_config()
    engine = create_engine(settings.DATABASE_URL)
    session = Session(engine)
    registry = SourceRegistry(settings=settings)
    persisted_versions = 0
    duplicate_versions = 0
    collected_items = 0
    source_results: dict[str, dict[str, Any]] = {}
    try:
        Base.metadata.create_all(engine)
        _sync_database_sources(session, registry)
        repository = DocumentRepository(session, storage_root=Path("storage"), storage_config=settings)
        request_guards = RequestGuards.from_source_registry(
            registry,
            settings=settings,
        )
        rate_limiter = SourceRateLimiter(
            {
                source.source_id: source_value(source, "rate_limit", {})
                for source in registry.active_sources
            }
        )
        for source in registry.active_sources:
            request_guards.validate_url(_collection_target(source))
            result = registry.collect(source.source_id)
            robots_text = _load_robots_text(
                registry,
                source,
                request_guards=request_guards,
                rate_limiter=rate_limiter,
                settings=settings,
            )
            collected_items += len(result.items)
            created = 0
            duplicates = 0
            access_denied = 0
            for item in result.items:
                request_guards.validate_url(_document_target(item, source))
                rate_limiter.acquire_or_wait(
                    source.source_id,
                    source_value(source, "rate_limit", {}),
                )
                decision = _evaluate_document_robots(
                    registry,
                    source,
                    item,
                    robots_text,
                )
                original_url = _item_url(item, source)
                existing = repository.get_document(source.source_id, original_url)
                if (
                    not decision.allowed
                    and existing is not None
                    and existing.collection_status == "collected"
                ):
                    # Older runs may have recorded an allowed terminal state.
                    # Do not force an invalid backward state transition; keep
                    # that historical row immutable and deny this observation.
                    duplicates += 1
                    access_denied += 1
                    continue
                raw_body = _raw_body(item)
                normalized_body = _normalized_body(item)
                ingestion = repository.ingest_document(
                    source_id=source.source_id,
                    original_url=original_url,
                    title=str(item.get("title") or source.name),
                    author_or_org=_author_text(item.get("authors")) or source.name,
                    published_at=_parse_datetime(item.get("published_at")),
                    language="en",
                    document_type=str(source.get("type") or "source_document"),
                    license=_license_text(item.get("license"))
                    or str(source_value(source, "license_notes", "")),
                    source_version_ref=(
                        item.get("source_version_ref")
                        or result.metadata.get("commit_sha")
                        or result.content_hash
                    ),
                    metadata=_item_metadata(item, result, source, decision=decision),
                    raw_body=raw_body if decision.allowed else None,
                    normalized_body=normalized_body if decision.allowed else None,
                    content_hash=_content_hash(item),
                    body_hash=_body_hash(item),
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
            persisted_versions += created
            duplicate_versions += duplicates
            _persist_source_cursor(session, source)
            source_results[source.source_id] = {
                "items": len(result.items),
                "versions_created": created,
                "duplicates": duplicates,
                "access_denied": access_denied,
                "cursor": dict(result.cursor) if result.cursor is not None else None,
            }
        session.commit()
    finally:
        session.close()
        registry.close()
        engine.dispose()

    print(
        json.dumps(
            {
                "phase": "0+1",
                "status": "dry_run",
                "llm_mode": settings.LLM_MODE,
                "delivery_mode": settings.DELIVERY_MODE,
                "active_fixture_sources": len(source_results),
                "collected_items": collected_items,
                "persisted_versions": persisted_versions,
                "duplicate_versions": duplicate_versions,
                "briefing_generated": False,
                "delivery_invoked": False,
                "sources": source_results,
            },
            sort_keys=True,
        )
    )
    return 0


def _collection_target(source: SourceRecord) -> str:
    return str(source_value(source, "base_url", ""))


def _document_target(item: Mapping[str, Any], source: SourceRecord) -> str:
    for key in ("request_url", "fetch_url", "collection_url", "target_url"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    collection_target = _collection_target(source)
    if urlsplit(collection_target).scheme.lower() != "fixture":
        for key in ("original_url", "canonical_url", "url"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return collection_target


def _item_url(item: Mapping[str, Any], source: SourceRecord) -> str:
    return str(
        item.get("original_url")
        or item.get("canonical_url")
        or item.get("url")
        or source_value(source, "original_url", source.base_url)
    )


def _source_metadata(source: SourceRecord) -> Mapping[str, Any]:
    value = source_value(source, "metadata", {})
    return value if isinstance(value, Mapping) else {}


def _robots_target(source: SourceRecord, document_url: str) -> str | None:
    base_url = _collection_target(source)
    parsed_base = urlsplit(base_url)
    robots_file = _source_metadata(source).get("robots_file")
    if parsed_base.scheme.lower() == "fixture":
        if not isinstance(robots_file, str) or not robots_file.strip():
            return None
        filename = robots_file.rsplit("/", 1)[-1].strip()
        return f"{base_url.rstrip('/')}/{filename}"
    if document_url:
        return urljoin(document_url, "/robots.txt")
    if base_url:
        return urljoin(base_url, "/robots.txt")
    return None


def _load_robots_text(
    registry: SourceRegistry,
    source: SourceRecord,
    *,
    request_guards: RequestGuards,
    rate_limiter: SourceRateLimiter,
    settings: Settings,
) -> str | None:
    document_url = str(source_value(source, "original_url", _collection_target(source)))
    target = _robots_target(source, document_url)
    if target is None:
        return None
    request_guards.validate_url(target)
    rate_limiter.acquire_or_wait(
        source.source_id,
        source_value(source, "rate_limit", {}),
    )
    transport = registry._transport_for(source)
    try:
        response = transport.get(
            target,
            headers={"Accept": "text/plain"},
            timeout=settings.request_timeout_seconds,
        )
        if not 200 <= response_status(response) < 300:
            return None
        return response_body(response).decode("utf-8", errors="replace")
    except Exception:
        # Robots failures are fail-closed by evaluate_robots below.  Keep the
        # collection path deterministic without falling back to static policy.
        return None


def _evaluate_document_robots(
    registry: SourceRegistry,
    source: SourceRecord,
    item: Mapping[str, Any],
    robots_text: str | None,
) -> RobotsDecision:
    document_url = _item_url(item, source)
    access_policy = source_value(source, "access_policy", {})
    user_agent = source_value(
        access_policy,
        "user_agent",
        source_value(registry.policy, "default_user_agent", "*"),
    )
    return evaluate_robots(
        robots_text,
        document_url,
        user_agent=str(user_agent or "*"),
        policy=access_policy,
        required=True,
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None


def _author_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, Mapping):
            return str(first.get("name") or "").strip()
        return str(first).strip()
    if isinstance(value, Mapping):
        return str(value.get("name") or "").strip()
    return ""


def _license_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_record(item: Mapping[str, Any]) -> bytes:
    excluded = {
        "raw_body",
        "normalized_body",
        "body_hash",
        "raw_record",
    }
    record = {key: value for key, value in item.items() if key not in excluded}
    return json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode(
        "utf-8"
    )


def _raw_body(item: Mapping[str, Any]) -> str:
    value = item.get("raw_body")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str) and value:
        return value
    raw_record = item.get("raw_record")
    if isinstance(raw_record, Mapping):
        return json.dumps(raw_record, ensure_ascii=False, sort_keys=True, default=str)
    return _json_record(item).decode("utf-8")


def _normalized_body(item: Mapping[str, Any]) -> str:
    explicit = item.get("normalized_body")
    if isinstance(explicit, bytes):
        return explicit.decode("utf-8", errors="replace")
    if isinstance(explicit, str) and explicit:
        return explicit
    value = item.get("body") or item.get("description") or item.get("summary")
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str) and value:
        return sanitize_html(value)
    return sanitize_html(_json_record(item))


def _body_hash(item: Mapping[str, Any]) -> str:
    supplied = item.get("body_hash")
    if isinstance(supplied, str) and supplied:
        return supplied
    return f"sha256:{sha256(_raw_body(item).encode('utf-8')).hexdigest()}"


def _content_hash(item: Mapping[str, Any]) -> str:
    supplied = item.get("content_hash")
    if isinstance(supplied, str) and supplied:
        return supplied
    return f"sha256:{sha256(_normalized_body(item).encode('utf-8')).hexdigest()}"


def _item_metadata(
    item: Mapping[str, Any],
    result: Any,
    source: SourceRecord,
    *,
    decision: RobotsDecision | None = None,
) -> dict[str, Any]:
    metadata = item.get("metadata")
    selected = dict(metadata) if isinstance(metadata, Mapping) else {}
    selected.update(
        {
            "source_id": source.source_id,
            "connector_type": source.connector_type,
            "collector_status": result.status_code,
            "sanitized": True,
        }
    )
    if decision is not None:
        selected.update(
            {
                "robots_allowed": decision.robots_allowed,
                "robots_rule_matched": decision.robots_rule_matched,
                "robots_evaluated_at": (
                    decision.robots_evaluated_at.isoformat()
                    if decision.robots_evaluated_at is not None
                    else None
                ),
                "access_decision_reason": decision.access_decision_reason,
            }
        )
    return selected


def _cursor_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
    return str(value)


def _sync_database_sources(session: Session, registry: SourceRegistry) -> None:
    for source in registry.active_sources:
        record = session.get(Source, source.source_id)
        if record is None:
            record = Source(
                source_id=source.source_id,
                name=source.name,
                type=source.type,
                base_url=source.base_url,
                connector_type=source.connector_type,
                active=bool(source.active),
                access_policy=dict(source.get("access_policy") or {}),
                robots_allowed=source.get("robots_allowed", "unknown"),
                robots_rule_matched=source.get("robots_rule_matched"),
                robots_evaluated_at=_parse_datetime(source.get("robots_evaluated_at")),
                access_decision_reason=source.get("access_decision_reason"),
                terms_reference=source.get("terms_reference"),
                license_notes=source.get("license_notes"),
                rate_limit=dict(source.get("rate_limit") or {}),
                schedule=source.get("schedule"),
                trust_tier=source.get("trust_tier", "unknown"),
                metadata=dict(source.get("metadata") or {}),
            )
            session.add(record)
        elif record.cursor:
            try:
                source["cursor"] = json.loads(record.cursor)
            except (TypeError, json.JSONDecodeError):
                source["cursor"] = record.cursor
    session.flush()


def _persist_source_cursor(session: Session, source: SourceRecord) -> None:
    record = session.get(Source, source.source_id)
    if record is None:
        return
    cursor = source_value(source, "cursor")
    if cursor is not None:
        record.cursor = _cursor_json(cursor)
    record.last_success_at = utc_now()


def create_review_app(settings: Settings | None = None) -> Any:
    """Create minimal local review API app; import web framework lazily."""

    try:
        from fastapi import FastAPI, Header, HTTPException
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RuntimeError("review-api requires fastapi") from exc

    active_settings = settings or load_config()
    app = FastAPI(title="scalping-briefing review API", version=__version__)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "binding": active_settings.REVIEW_API_BIND}

    @app.get("/reviews")
    def reviews(
        authorization: str | None = Header(default=None),
        x_review_token: str | None = Header(default=None),
    ) -> dict[str, list[Any]]:
        configured_token = active_settings.REVIEW_API_TOKEN
        presented_token = x_review_token
        if presented_token is None and authorization and authorization.startswith("Bearer "):
            presented_token = authorization[7:]
        if not configured_token or presented_token != configured_token:
            raise HTTPException(status_code=401, detail="review token required")
        return {"reviews": []}

    return app


def run_review_api() -> int:
    """Start local-only review API after configuration safety checks."""

    settings = load_config()
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RuntimeError("review-api requires uvicorn") from exc
    uvicorn.run(
        create_review_app(settings),
        host=settings.REVIEW_API_BIND,
        port=8000,
        log_config=None,
    )
    return 0


__all__ = [
    "CONFIG_KEYS",
    "Config",
    "ConfigError",
    "Settings",
    "create_review_app",
    "load_config",
    "load_settings",
    "run_briefing",
    "run_review_api",
]
