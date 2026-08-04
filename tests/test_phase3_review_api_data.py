from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scalping_briefing import create_review_app
from scalping_briefing.config import load_config
from scalping_briefing.pipeline import state_machine
from scalping_briefing.models import (
    Base,
    Document,
    DocumentVersion,
    Evidence,
    Source,
    StrategyCandidate,
)
from scalping_briefing.pipeline.validate import CORE_FIELDS
from scalping_briefing.review import api


TOKEN = "review-secret"


def _settings(database_path: Path):
    return load_config(
        environ={
            "DATABASE_URL": f"sqlite:///{database_path}",
            "REVIEW_API_TOKEN": TOKEN,
        }
    )


def _get_app(database_path: Path):
    settings = _settings(database_path)
    engine = create_engine(settings.DATABASE_URL)
    Base.metadata.create_all(engine)
    return create_review_app(settings, engine=engine), engine


def _request(
    app: Any,
    method: str,
    path: str,
    *,
    authenticated: bool = True,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        headers = {"X-Review-Token": TOKEN} if authenticated else None
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(
                method,
                path,
                headers=headers,
                json=json,
            )

    return asyncio.run(run())


def _seed_candidate(engine) -> None:
    source = Source(
        source_id="api-source",
        name="API source",
        type="fixture",
        base_url="https://example.invalid",
        connector_type="fixture",
        active=True,
        metadata={},
    )
    document = Document(
        document_id="api-document",
        source_id=source.source_id,
        original_url="https://example.invalid/original",
        canonical_url="https://example.invalid/canonical",
        title="API document",
        metadata={},
    )
    version = DocumentVersion(
        document_version_id="api-version",
        document_id=document.document_id,
        content_hash="api-hash",
        metadata={},
    )
    candidate = StrategyCandidate(
        candidate_id="api-candidate",
        canonical_name="API candidate",
        summary="A candidate for API tests.",
        core_hypothesis="The documented condition matters.",
        core_hypothesis_status="explicit",
        signal_inputs=["condition"],
        signal_inputs_status="explicit",
        entry_logic="Enter after confirmation.",
        entry_logic_status="explicit",
        exit_logic="Exit on reversal.",
        exit_logic_status="explicit",
        required_data=["quotes"],
        required_data_status="explicit",
        risk_notes="Latency risk.",
        risk_notes_status="explicit",
        field_status={field: "explicit" for field in CORE_FIELDS},
        relevance_status="relevant",
        review_status="needs_review",
        document_version_ids=[version.document_version_id],
        metadata={},
    )
    evidence = [
        Evidence(
            evidence_id=f"api-evidence-{field}",
            document_version_id=version.document_version_id,
            strategy_candidate_id=candidate.candidate_id,
            field_name=field,
            quote=f"Evidence quote for {field}.",
            section_or_locator=field,
            captured_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
        for field in CORE_FIELDS
    ]
    with Session(engine) as session:
        session.add_all([source, document, version, candidate, *evidence])
        session.commit()


def test_review_data_routes_delegate_to_the_matching_service_method(tmp_path, monkeypatch) -> None:
    app, engine = _get_app(tmp_path / "delegation.sqlite3")
    calls: list[tuple[str, Any]] = []
    sessions: list[object] = []

    class FakeService:
        def __init__(self, session) -> None:
            sessions.append(session)

        def list_candidates(self, status=None):
            calls.append(("list_candidates", status))
            return [{"candidate_id": "candidate-1"}]

        def get_candidate(self, candidate_id):
            calls.append(("get_candidate", candidate_id))
            return {
                "candidate_id": candidate_id,
                "source_url": "https://example.invalid/source",
                "document_version_id": "version-1",
                "evidence": [{"quote": "Evidence quote."}],
            }

        def record_decision(self, candidate_id, reviewer_id, decision, comment=None):
            calls.append(
                ("record_decision", (candidate_id, reviewer_id, decision, comment))
            )
            return {
                "review_id": "review-1",
                "reviewer_id": reviewer_id,
                "decision": decision,
            }

    monkeypatch.setattr(api, "ReviewService", FakeService)
    try:
        assert _request(app, "GET", "/reviews").status_code == 200
        detail = _request(app, "GET", "/reviews/candidate-1")
        decision = _request(
            app,
            "POST",
            "/reviews/candidate-1/decision",
            json={
                "reviewer_id": "reviewer-1",
                "decision": "approved",
                "comment": "checked",
            },
        )
    finally:
        engine.dispose()

    assert detail.status_code == 200
    assert detail.json()["document_version_id"] == "version-1"
    assert detail.json()["evidence"][0]["quote"] == "Evidence quote."
    assert decision.status_code == 200
    assert calls == [
        ("list_candidates", None),
        ("get_candidate", "candidate-1"),
        ("record_decision", ("candidate-1", "reviewer-1", "approved", "checked")),
    ]
    assert len({id(session) for session in sessions}) == 3


def test_review_detail_returns_traceability_and_404_for_unknown_candidate(tmp_path) -> None:
    app, engine = _get_app(tmp_path / "detail.sqlite3")
    try:
        _seed_candidate(engine)
        response = _request(app, "GET", "/reviews/api-candidate")
        missing = _request(app, "GET", "/reviews/does-not-exist")
    finally:
        engine.dispose()

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_url"] == "https://example.invalid/original"
    assert payload["document_version_id"] == "api-version"
    assert payload["evidence"][0]["quote"].startswith("Evidence quote")
    assert missing.status_code == 404


def test_health_is_public_but_all_review_data_routes_require_the_static_token(tmp_path) -> None:
    app, engine = _get_app(tmp_path / "auth.sqlite3")
    try:
        assert _request(app, "GET", "/health", authenticated=False).status_code == 200
        for method, path, body in (
            ("GET", "/reviews", None),
            ("GET", "/reviews/unknown", None),
            (
                "POST",
                "/reviews/unknown/decision",
                {"reviewer_id": "reviewer-1", "decision": "approved"},
            ),
        ):
            assert (
                _request(
                    app,
                    method,
                    path,
                    authenticated=False,
                    json=body,
                ).status_code
                == 401
            )
    finally:
        engine.dispose()


def test_every_non_health_api_route_declares_review_token_dependency(tmp_path) -> None:
    app, engine = _get_app(tmp_path / "route-contract.sqlite3")
    try:
        for route in app.routes:
            if getattr(route, "path", None) == "/health":
                continue
            if not isinstance(route, APIRoute):
                continue
            assert any(
                getattr(dependency.call, "__name__", None)
                == "require_review_token"
                for dependency in route.dependant.dependencies
            ), route.path
    finally:
        engine.dispose()


def test_decision_endpoint_persists_state_machine_transition_in_new_session(
    tmp_path,
) -> None:
    app, engine = _get_app(tmp_path / "decision-persistence.sqlite3")
    _seed_candidate(engine)
    try:
        with Session(engine) as session:
            candidate = session.get(StrategyCandidate, "api-candidate")
            assert candidate is not None
            original_status = candidate.review_status

        decision = "approved"
        response = _request(
            app,
            "POST",
            "/reviews/api-candidate/decision",
            json={
                "reviewer_id": "reviewer-1",
                "decision": decision,
                "comment": "checked",
            },
        )
        assert response.status_code == 200
        assert response.json()["review"]["decision"] == decision

        with Session(engine) as new_session:
            persisted = new_session.get(StrategyCandidate, "api-candidate")
            assert persisted is not None
            persisted_status = persisted.review_status

        assert persisted_status == decision
        assert state_machine.can_transition(original_status, persisted_status)
    finally:
        engine.dispose()


def test_decision_requires_non_empty_reviewer_id(tmp_path) -> None:
    app, engine = _get_app(tmp_path / "decision.sqlite3")
    try:
        response = _request(
            app,
            "POST",
            "/reviews/unknown/decision",
            json={"reviewer_id": "   ", "decision": "approved"},
        )
    finally:
        engine.dispose()

    assert response.status_code == 422
