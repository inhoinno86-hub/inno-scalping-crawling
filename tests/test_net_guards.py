from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable

import httpx
import pytest

from scalping_briefing.net.guards import (
    HostNotAllowedError,
    MimeTypeNotAllowedError,
    RedirectLimitExceededError,
    ResponseTooLargeError,
    RequestTimeoutError,
    SSRFError,
)
from scalping_briefing.net.transport import FixtureTransport, HTTPTransport


PUBLIC_IP = "93.184.216.34"


def resolver_for(address: str) -> Callable[..., list[tuple[object, ...]]]:
    parsed = ipaddress.ip_address(address)

    def resolve(host: str, port: int, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        if parsed.version == 6:
            sockaddr: tuple[object, ...] = (address, port, 0, 0)
            family = socket.AF_INET6
        else:
            sockaddr = (address, port)
            family = socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", sockaddr)]

    return resolve


def make_http_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fixture_and_live_transports_share_bounded_request_contract() -> None:
    fixture = FixtureTransport()
    fixture_response = fixture.get("fixture://fixture_rss_blog/response.xml")

    assert fixture_response.status_code == 200
    assert fixture_response.headers["content-type"] == "application/rss+xml"
    assert b"<rss" in fixture_response.content
    v2_response = fixture.get("fixture://fixture_atom_research/response.v2.xml")
    assert v2_response.headers["etag"] == '"fixture-atom-v2"'
    assert all(hasattr(fixture, name) for name in ("request", "get", "close"))

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user-agent"] = request.headers["user-agent"]
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b'{"ok": true}',
            request=request,
        )

    client = make_http_client(handler)
    live = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
    )
    live_response = live.get("https://allowed.example/data")

    assert live_response.status_code == 200
    assert live_response.json() == {"ok": True}
    assert seen["user-agent"] == "scalping-briefing-fixture/0.1 (+offline-test)"

    live.close()
    fixture.close()


def test_fixture_transport_cannot_escape_static_fixture_root() -> None:
    with pytest.raises((HostNotAllowedError, ValueError)):
        FixtureTransport().get("fixture://fixture_rss_blog/../../config/default.toml")


def test_allowlist_rejection_happens_before_http_client_call() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"should not be reached", request=request)

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
    )

    with pytest.raises(HostNotAllowedError):
        transport.get("https://blocked.example/data")

    assert calls == []
    transport.close()


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "172.16.0.8",
        "192.168.1.8",
        "169.254.1.8",
        "100.64.0.8",
        "::1",
        "fe80::1",
        "fd00::8",
    ],
)
def test_resolved_forbidden_addresses_are_rejected_before_request(
    address: str,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"should not be reached", request=request)

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(address),
    )

    with pytest.raises(SSRFError):
        transport.get("https://allowed.example/data")

    assert calls == []
    transport.close()


def test_redirect_target_is_revalidated_against_allowlist() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "https://blocked.example/private"},
            request=request,
        )

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
    )

    with pytest.raises(HostNotAllowedError):
        transport.get("https://allowed.example/start")

    assert calls == ["https://allowed.example/start"]
    transport.close()


def test_redirect_processing_stops_after_three_redirects() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        redirect_number = len(calls)
        return httpx.Response(
            302,
            headers={"location": f"https://allowed.example/redirect-{redirect_number}"},
            request=request,
        )

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
        max_redirects=3,
    )

    with pytest.raises(RedirectLimitExceededError):
        transport.get("https://allowed.example/start")

    assert len(calls) == 4
    transport.close()


def test_streaming_response_size_limit_aborts_an_oversized_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"01234567890",
            request=request,
        )

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
        response_max_bytes=10,
    )

    with pytest.raises(ResponseTooLargeError):
        transport.get("https://allowed.example/large")

    transport.close()


def test_http_timeout_is_mapped_to_transport_timeout_and_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("fixture timeout", request=request)

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
    )

    assert transport.request_timeout_seconds == 20
    with pytest.raises(RequestTimeoutError):
        transport.get("https://allowed.example/slow")

    transport.close()


def test_mime_policy_rejects_unapproved_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"not an approved source response",
            request=request,
        )

    client = make_http_client(handler)
    transport = HTTPTransport(
        allowed_urls=["https://allowed.example"],
        client=client,
        resolver=resolver_for(PUBLIC_IP),
    )

    with pytest.raises(MimeTypeNotAllowedError):
        transport.get("https://allowed.example/binary")

    transport.close()
