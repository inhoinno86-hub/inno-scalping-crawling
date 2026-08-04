"""Authenticated FastAPI endpoints for the local candidate review flow."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Mapping
from typing import Any

from pydantic import BaseModel
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import Session, sessionmaker

from scalping_briefing.config import Settings, load_config
from scalping_briefing.models import Base
from scalping_briefing.review.service import ReviewService


class DecisionRequest(BaseModel):
    """Payload accepted by the candidate decision endpoint."""

    reviewer_id: str
    decision: str
    comment: str | None = None


def _record_payload(value: Any) -> Any:
    """Return JSON-safe data for ORM records and service test doubles."""

    from fastapi.encoders import jsonable_encoder

    if isinstance(value, Mapping):
        return jsonable_encoder(value)

    try:
        mapper = sqlalchemy_inspect(value).mapper
    except (AttributeError, TypeError, NoInspectionAvailable):
        mapper = None
    if mapper is not None:
        payload = {
            ("metadata" if attribute.key == "metadata_json" else attribute.key): getattr(
                value,
                attribute.key,
            )
            for attribute in mapper.column_attrs
        }
        return jsonable_encoder(payload)

    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        payload: dict[str, Any] = {}
        for name, item in attributes.items():
            if name.startswith("_"):
                continue
            output_name = "metadata" if name == "metadata_json" else name
            payload[output_name] = item
        if payload:
            return jsonable_encoder(payload)

    return jsonable_encoder(value)


def create_review_app(
    settings: Settings | None = None,
    *,
    engine: Engine | None = None,
) -> Any:
    """Create the authenticated local review API.

    The engine belongs to the app, while each request gets a fresh SQLAlchemy
    ``Session`` from the app-local factory.  No session is shared globally.
    ``engine`` is injectable for tests and local application composition.
    """

    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
    except ImportError as exc:  # pragma: no cover - dependency installation issue
        raise RuntimeError("review-api requires fastapi") from exc

    active_settings = settings or load_config()
    configured_token = active_settings.REVIEW_API_TOKEN
    if not isinstance(configured_token, str) or not configured_token.strip():
        raise RuntimeError("review-api requires REVIEW_API_TOKEN")

    review_engine = engine or create_engine(str(active_settings.DATABASE_URL))
    # A fresh checkout may not have been migrated yet.  Creating the existing
    # schema keeps the offline review endpoint usable without adding a schema
    # or migration change to this API task.
    Base.metadata.create_all(review_engine)
    session_factory = sessionmaker(
        bind=review_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    app = FastAPI(title="scalping-briefing review API", version="0.1.0")
    app.state.review_engine = review_engine
    app.state.review_session_factory = session_factory

    async def get_review_session() -> AsyncIterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    async def require_review_token(
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_review_token: str | None = Header(
            default=None,
            alias="X-Review-Token",
        ),
    ) -> None:
        presented_token = x_review_token
        if presented_token is None and authorization:
            scheme, separator, value = authorization.partition(" ")
            if separator and scheme.lower() == "bearer":
                presented_token = value
        if not isinstance(presented_token, str) or not secrets.compare_digest(
            presented_token,
            configured_token,
        ):
            raise HTTPException(status_code=401, detail="review token required")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "binding": active_settings.REVIEW_API_BIND}

    @app.get("/reviews", dependencies=[Depends(require_review_token)])
    async def reviews(
        status: str | None = None,
        session: Session = Depends(get_review_session),
    ) -> dict[str, list[Any]]:
        service = ReviewService(session)
        candidates = (
            service.list_candidates(status=status)
            if status is not None
            else service.list_candidates()
        )
        return {"reviews": [_record_payload(candidate) for candidate in candidates]}

    @app.get(
        "/reviews/{candidate_id}",
        dependencies=[Depends(require_review_token)],
    )
    async def review_detail(
        candidate_id: str,
        session: Session = Depends(get_review_session),
    ) -> Any:
        result = ReviewService(session).get_candidate(candidate_id)
        if result is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        return _record_payload(result)

    @app.post(
        "/reviews/{candidate_id}/decision",
        dependencies=[Depends(require_review_token)],
    )
    async def review_decision(
        candidate_id: str,
        request: DecisionRequest,
        session: Session = Depends(get_review_session),
    ) -> dict[str, Any]:
        if not request.reviewer_id.strip():
            raise HTTPException(
                status_code=422,
                detail="reviewer_id must be a non-empty string",
            )

        service = ReviewService(session)
        try:
            review = service.record_decision(
                candidate_id,
                request.reviewer_id,
                request.decision,
                request.comment,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            if str(exc).startswith("strategy candidate not found:"):
                raise HTTPException(
                    status_code=404,
                    detail="candidate not found",
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {"review": _record_payload(review)}

    return app


__all__ = ["DecisionRequest", "ReviewService", "create_review_app"]
