from __future__ import annotations

import asyncio

import httpx
import pytest

from scalping_briefing import create_review_app
from scalping_briefing.config import load_config


def _get(app: object, path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(request())


@pytest.mark.parametrize("token_value", [None, "", "   "])
def test_create_review_app_fails_without_token(token_value: str | None) -> None:
    environment = {} if token_value is None else {"REVIEW_API_TOKEN": token_value}
    settings = load_config(environ=environment)

    with pytest.raises(RuntimeError, match="REVIEW_API_TOKEN"):
        create_review_app(settings)


def test_health_is_reachable_without_token() -> None:
    app = create_review_app(load_config(environ={"REVIEW_API_TOKEN": "review-secret"}))

    response = _get(app, "/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_every_data_endpoint_requires_token() -> None:
    token = "review-secret"
    app = create_review_app(load_config(environ={"REVIEW_API_TOKEN": token}))

    response = _get(app, "/reviews")
    assert response.status_code == 401

    response = _get(app, "/reviews", headers={"X-Review-Token": token})
    assert response.status_code == 200
    assert response.json() == {"reviews": []}


def test_review_api_binds_loopback_by_default() -> None:
    settings = load_config(environ={"REVIEW_API_TOKEN": "review-secret"})
    app = create_review_app(settings)

    assert settings.REVIEW_API_BIND == "127.0.0.1"
    assert _get(app, "/health").json()["binding"] == "127.0.0.1"
