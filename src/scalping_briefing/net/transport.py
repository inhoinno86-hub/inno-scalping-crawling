"""Bounded fixture and live HTTP transports with one shared interface."""

from __future__ import annotations

import json
import mimetypes
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from .guards import (
    DEFAULT_USER_AGENT,
    RequestGuards,
    RequestTimeoutError,
    RequestGuardError,
    consume_limited,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "sources"


class TransportError(RequestGuardError):
    """Base class for transport-specific failures."""


class FixturePathError(TransportError):
    """A fixture URL did not resolve to an approved static fixture file."""


@dataclass(frozen=True)
class TransportResponse:
    """Small response value shared by fixture and live transports."""

    status_code: int
    url: str
    headers: Mapping[str, str]
    content: bytes

    @property
    def body(self) -> bytes:
        return self.content

    @property
    def text(self) -> str:
        content_type = self.headers.get("content-type", "")
        charset = "utf-8"
        for part in content_type.split(";")[1:]:
            key, separator, value = part.strip().partition("=")
            if separator and key.lower() == "charset" and value.strip():
                charset = value.strip().strip('"')
                break
        try:
            return self.content.decode(charset, errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))

    def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterable[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self) -> None:
        """Match the live response lifecycle; fixture responses need no action."""


class Transport(Protocol):
    """The bounded request contract used by source connectors."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        ...

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        ...

    def close(self) -> None:
        ...


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _fixture_allowlist(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return [
        f"fixture://{directory.name}"
        for directory in sorted(root.iterdir())
        if directory.is_dir() and (directory / "metadata.json").is_file()
    ]


def _safe_fixture_path(root: Path, source_root: Path, relative: str) -> Path:
    if not relative or relative.startswith("/"):
        raise FixturePathError("fixture response path must be relative")
    candidate = (source_root / relative).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_relative_to(source_root):
        raise FixturePathError("fixture response path escapes tests/fixtures/sources")
    if not candidate.is_file():
        raise FixturePathError(f"fixture response file does not exist: {relative}")
    return candidate


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FixturePathError(f"invalid fixture metadata: {path.name}") from exc
    if not isinstance(value, dict):
        raise FixturePathError(f"fixture metadata must be a JSON object: {path.name}")
    return value


def _content_type_for(path: Path, metadata: Mapping[str, Any]) -> str:
    if path.name == "robots.txt":
        return "text/plain"
    declared = metadata.get("content_type")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _fixture_header_file(
    source_root: Path,
    metadata: Mapping[str, Any],
    response_path: Path,
) -> Path | None:
    candidates: list[str] = []
    if ".v2." in response_path.name:
        candidates.append(response_path.name.replace(".v2.", ".v2.headers."))
    declared = metadata.get("headers_file")
    if isinstance(declared, str):
        candidates.append(declared)
        if ".v2." in response_path.name:
            candidates.insert(0, declared.replace(".", ".v2.", 1))
    for candidate in candidates:
        try:
            path = _safe_fixture_path(source_root.parent, source_root, candidate)
        except FixturePathError:
            continue
        if path.is_file():
            return path
    return None


class FixtureTransport:
    """Offline transport that reads only files below the static fixture root."""

    def __init__(
        self,
        fixture_root: str | Path = DEFAULT_FIXTURE_ROOT,
        allowed_urls: Iterable[str] | Mapping[str, Any] | str | None = None,
        *,
        allowed_hosts: Iterable[str] | Mapping[str, Any] | str | None = None,
        source_registry: Any = None,
        settings: Mapping[str, Any] | Any | None = None,
        response_max_bytes: int | None = None,
        request_timeout_seconds: float | None = None,
        max_redirects: int | None = None,
        allowed_mime_types: Iterable[str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        requested_root = Path(fixture_root).resolve()
        static_root = DEFAULT_FIXTURE_ROOT.resolve()
        if not requested_root.is_relative_to(static_root):
            raise FixturePathError(
                "FixtureTransport root must be below tests/fixtures/sources"
            )
        self.fixture_root = requested_root
        if allowed_urls is None and allowed_hosts is None and source_registry is None:
            allowed_urls = _fixture_allowlist(self.fixture_root)
        self.guards = RequestGuards(
            allowed_urls=allowed_urls,
            allowed_hosts=allowed_hosts,
            source_registry=source_registry,
            settings=settings,
            response_max_bytes=response_max_bytes,
            request_timeout_seconds=request_timeout_seconds,
            max_redirects=max_redirects,
            allowed_mime_types=allowed_mime_types,
            user_agent=(user_agent if user_agent is not None else DEFAULT_USER_AGENT),
        )
        self.last_request_headers: dict[str, str] = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        request_method = method.upper()
        if request_method not in {"GET", "HEAD"}:
            raise TransportError(f"FixtureTransport does not support {request_method}")
        parsed = self.guards.validate_url(url)
        if parsed.scheme.lower() != "fixture":
            raise TransportError("FixtureTransport only accepts fixture:// URLs")
        source_id = parsed.hostname
        if source_id is None:
            raise FixturePathError("fixture URL must contain a source id")
        source_root = _safe_fixture_path(
            self.fixture_root,
            self.fixture_root,
            f"{source_id}/metadata.json",
        ).parent
        metadata = _read_json(source_root / "metadata.json")
        response_name = self._response_name(parsed.path, parsed.query, metadata)
        response_path = _safe_fixture_path(self.fixture_root, source_root, response_name)
        body = response_path.read_bytes() if request_method == "GET" else b""
        response_headers = {
            "content-type": _content_type_for(response_path, metadata),
            "content-length": str(len(body)),
        }
        header_file = _fixture_header_file(source_root, metadata, response_path)
        if header_file is not None:
            extra_headers = _read_json(header_file)
            response_headers.update(
                {str(key).lower(): str(value) for key, value in extra_headers.items()}
            )
        self.last_request_headers = self.guards.prepare_headers(headers)
        self.guards.validate_mime(response_headers)
        self.guards.validate_content_length(response_headers)
        bounded_body = consume_limited(
            (body[offset : offset + 64 * 1024] for offset in range(0, len(body), 64 * 1024)),
            self.guards.response_max_bytes,
        )
        status_code = metadata.get("status_code", 200)
        if not isinstance(status_code, int):
            raise FixturePathError("fixture status_code must be an integer")
        return TransportResponse(
            status_code=status_code,
            url=url,
            headers=response_headers,
            content=bounded_body,
        )

    @staticmethod
    def _response_name(
        path: str,
        query: str,
        metadata: Mapping[str, Any],
    ) -> str:
        requested = unquote(path).lstrip("/")
        if not requested:
            query_file = parse_qs(query).get("file", [""])[0]
            requested = unquote(query_file)
        if requested:
            return requested
        response_file = metadata.get("response_file")
        if isinstance(response_file, str) and response_file:
            return response_file
        response_files = metadata.get("response_files")
        if isinstance(response_files, list) and response_files and isinstance(response_files[0], str):
            return response_files[0]
        raise FixturePathError("fixture metadata does not declare a response file")

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        return self.request("GET", url, headers=headers, timeout=timeout)

    def close(self) -> None:
        return None

    def __enter__(self) -> "FixtureTransport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class HTTPTransport:
    """Live HTTP transport with manual, revalidated redirect handling."""

    def __init__(
        self,
        allowed_urls: Iterable[str] | Mapping[str, Any] | str | None = None,
        *,
        allowed_hosts: Iterable[str] | Mapping[str, Any] | str | None = None,
        source_registry: Any = None,
        settings: Mapping[str, Any] | Any | None = None,
        client: Any = None,
        resolver: Any = None,
        response_max_bytes: int | None = None,
        request_timeout_seconds: float | None = None,
        max_redirects: int | None = None,
        allowed_mime_types: Iterable[str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.guards = RequestGuards(
            allowed_urls=allowed_urls,
            allowed_hosts=allowed_hosts,
            source_registry=source_registry,
            settings=settings,
            resolver=resolver,
            response_max_bytes=response_max_bytes,
            request_timeout_seconds=request_timeout_seconds,
            max_redirects=max_redirects,
            allowed_mime_types=allowed_mime_types,
            user_agent=(user_agent if user_agent is not None else DEFAULT_USER_AGENT),
        )
        self.request_timeout_seconds = self.guards.request_timeout_seconds
        self._client = client or httpx.Client()
        self._closed = False

    def _timeout(self, requested: float | None) -> float:
        if requested is None:
            return self.request_timeout_seconds
        value = float(requested)
        if value <= 0:
            raise ValueError("timeout must be positive")
        return min(value, self.request_timeout_seconds)

    @staticmethod
    def _response_chunks(response: Any) -> Iterable[bytes]:
        iterator = getattr(response, "iter_bytes", None)
        if iterator is not None:
            return iterator()
        iterator = getattr(response, "iter_raw", None)
        if iterator is not None:
            return iterator()
        content = getattr(response, "content", None)
        if isinstance(content, bytes):
            return (content,)
        raise TransportError("HTTP response does not expose a byte stream")

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        if self._closed:
            raise TransportError("HTTPTransport is closed")
        current_url = url
        redirect_count = 0
        request_method = method.upper()
        effective_timeout = self._timeout(timeout)
        while True:
            self.guards.validate_url(current_url)
            request_headers = self.guards.prepare_headers(headers)
            try:
                stream = self._client.stream(
                    request_method,
                    current_url,
                    headers=request_headers,
                    timeout=effective_timeout,
                    follow_redirects=False,
                )
                with stream as response:
                    response_headers = {
                        str(key).lower(): str(value)
                        for key, value in response.headers.items()
                    }
                    status_code = int(response.status_code)
                    location = _header(response_headers, "location")
                    if 300 <= status_code < 400 and location is not None:
                        current_url = self.guards.redirect_target(
                            current_url,
                            location,
                            redirect_count,
                        )
                        redirect_count += 1
                        continue
                    self.guards.validate_mime(response_headers)
                    self.guards.validate_content_length(response_headers)
                    body = consume_limited(
                        self._response_chunks(response),
                        self.guards.response_max_bytes,
                    )
                    return TransportResponse(
                        status_code=status_code,
                        url=current_url,
                        headers=response_headers,
                        content=body,
                    )
            except (httpx.TimeoutException, TimeoutError) as exc:
                if isinstance(exc, RequestTimeoutError):
                    raise
                raise RequestTimeoutError(
                    f"request exceeded request_timeout_seconds={self.request_timeout_seconds}"
                ) from exc

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        return self.request("GET", url, headers=headers, timeout=timeout)

    def close(self) -> None:
        if not self._closed:
            self._client.close()
            self._closed = True

    def __enter__(self) -> "HTTPTransport":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


LiveHTTPTransport = HTTPTransport
HttpTransport = HTTPTransport
Response = TransportResponse


__all__ = [
    "DEFAULT_FIXTURE_ROOT",
    "FixturePathError",
    "FixtureTransport",
    "HTTPTransport",
    "HttpTransport",
    "LiveHTTPTransport",
    "Response",
    "Transport",
    "TransportError",
    "TransportResponse",
]
