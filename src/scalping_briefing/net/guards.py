"""Fail-closed request and response guards for source collection.

The guard layer owns policy decisions that must happen before a transport can
issue a request.  It deliberately accepts a small, dependency-free interface
so the same checks can be used by fixture and live transports.
"""

from __future__ import annotations

import ipaddress
import math
import socket
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urljoin, urlsplit


DEFAULT_RESPONSE_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_MAX_REDIRECTS = 3
DEFAULT_USER_AGENT = "scalping-briefing-fixture/0.1 (+offline-test)"
DEFAULT_ALLOWED_MIME_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/json",
        "application/rss+xml",
        "application/xml",
        "text/html",
        "text/plain",
        "text/xml",
    }
)

_Resolver = Callable[..., Iterable[Any]]

_FORBIDDEN_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
)


class RequestGuardError(ValueError):
    """Base class for a request or response policy violation."""


class HostNotAllowedError(RequestGuardError):
    """The target is not an exact host in the configured source allowlist."""


class UnsupportedSchemeError(RequestGuardError):
    """The transport does not support the target URL scheme."""


class SSRFError(RequestGuardError):
    """The target or one of its resolved addresses is not safe to contact."""


class RedirectLimitExceededError(RequestGuardError):
    """The configured redirect limit was reached."""


class ResponseTooLargeError(RequestGuardError):
    """A streamed response exceeded the configured byte limit."""


class RequestTimeoutError(RequestGuardError, TimeoutError):
    """The transport request exceeded its configured timeout."""


class MimeTypeNotAllowedError(RequestGuardError):
    """The response Content-Type is outside the approved MIME policy."""


@dataclass(frozen=True)
class _AllowRule:
    scheme: str | None
    host: str
    port: int | None


def _setting(settings: Mapping[str, Any] | Any | None, name: str, default: Any) -> Any:
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        try:
            return settings.get(name, default)
        except KeyError:
            return default
    return getattr(settings, name, default)


def _normalise_host(host: str) -> str:
    if not isinstance(host, str):
        raise HostNotAllowedError("URL host must be a string")
    value = host.strip().rstrip(".").lower()
    if not value:
        raise HostNotAllowedError("URL host must not be empty")
    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        pass
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HostNotAllowedError(f"invalid URL host: {host!r}") from exc


def _parse_rule(value: str) -> _AllowRule:
    if not isinstance(value, str):
        raise HostNotAllowedError("source allowlist entries must be strings")
    text = value.strip()
    if not text:
        raise HostNotAllowedError("empty source allowlist entry")

    has_scheme = "://" in text
    candidate = urlsplit(
        text if has_scheme or text.startswith("//") else f"//{text}"
    )
    try:
        host = candidate.hostname
        port = candidate.port
    except ValueError as exc:
        raise HostNotAllowedError(f"invalid source allowlist entry: {value!r}") from exc
    if host is None:
        raise HostNotAllowedError(f"source allowlist entry has no host: {value!r}")
    if candidate.username is not None or candidate.password is not None:
        raise HostNotAllowedError("source allowlist entries must not contain credentials")
    scheme = candidate.scheme.lower() if has_scheme and candidate.scheme else None
    if scheme not in {None, "http", "https", "fixture"}:
        raise UnsupportedSchemeError(f"unsupported allowlist scheme: {scheme}")
    return _AllowRule(scheme=scheme, host=_normalise_host(host), port=port)


def _iter_allowlist_values(value: Any) -> Iterable[str]:
    """Extract URL entries from a Source Registry-shaped object."""

    if value is None:
        return
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        if "sources" in value:
            yield from _iter_allowlist_values(value["sources"])
        if "allowlist" in value:
            yield from _iter_allowlist_values(value["allowlist"])
        access_policy = value.get("access_policy")
        if access_policy is not None:
            yield from _iter_allowlist_values(access_policy)
        if "base_url" in value and isinstance(value["base_url"], str):
            yield value["base_url"]
        if "url" in value and isinstance(value["url"], str):
            yield value["url"]
        known_keys = {"sources", "allowlist", "access_policy", "base_url", "url"}
        for key, item in value.items():
            if key not in known_keys:
                if isinstance(key, str) and (
                    "://" in key or key.startswith("//") or "." in key
                ):
                    yield key
                if isinstance(item, (Mapping, Iterable)) and not isinstance(
                    item, (str, bytes, bytearray)
                ):
                    yield from _iter_allowlist_values(item)
        return
    for attribute in ("sources", "allowlist", "access_policy"):
        nested = getattr(value, attribute, None)
        if nested is not None:
            yield from _iter_allowlist_values(nested)
    for attribute in ("base_url", "url"):
        entry = getattr(value, attribute, None)
        if isinstance(entry, str):
            yield entry
    if any(
        getattr(value, attribute, None) is not None
        for attribute in ("sources", "allowlist", "access_policy", "base_url", "url")
    ):
        return
    if isinstance(value, Iterable):
        for item in value:
            yield from _iter_allowlist_values(item)


def _rules_from(
    allowed_urls: Iterable[str] | Mapping[str, Any] | str | None,
    source_registry: Any,
) -> tuple[_AllowRule, ...]:
    entries = list(_iter_allowlist_values(allowed_urls))
    entries.extend(_iter_allowlist_values(source_registry))
    rules: list[_AllowRule] = []
    seen: set[_AllowRule] = set()
    for entry in entries:
        rule = _parse_rule(entry)
        if rule not in seen:
            seen.add(rule)
            rules.append(rule)
    return tuple(rules)


def _default_port(scheme: str) -> int | None:
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _is_forbidden_ip(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except (TypeError, ValueError):
        return True

    # `is_global` is the fail-closed baseline.  The explicit checks document
    # the policy and cover Python-version differences for special ranges.
    mapped = getattr(ip, "ipv4_mapped", None)
    return (
        not ip.is_global
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or getattr(ip, "is_site_local", False)
        or any(ip in network for network in _FORBIDDEN_NETWORKS)
        or (mapped is not None and _is_forbidden_ip(mapped))
    )


def is_forbidden_ip(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return whether an IP address belongs to a forbidden SSRF range."""

    return _is_forbidden_ip(address)


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _iter_resolved_addresses(resolved: Any) -> Iterable[Any]:
    """Extract addresses from common resolver and test-double result shapes."""

    ip_types = (str, ipaddress.IPv4Address, ipaddress.IPv6Address)
    if isinstance(resolved, ip_types):
        yield resolved
        return
    try:
        items = iter(resolved)
    except TypeError:
        return
    for item in items:
        if isinstance(item, ip_types):
            yield item
            continue
        if isinstance(item, Mapping):
            for key in ("address", "ip", "host"):
                address = item.get(key)
                if isinstance(address, ip_types):
                    yield address
                    break
            continue
        if not isinstance(item, Sequence) or isinstance(
            item, (bytes, bytearray, str)
        ):
            continue
        if len(item) >= 5:
            sockaddr = item[4]
            if isinstance(sockaddr, ip_types):
                yield sockaddr
            elif isinstance(sockaddr, Sequence) and not isinstance(
                sockaddr, (bytes, bytearray, str)
            ):
                if sockaddr:
                    yield sockaddr[0]
            continue
        if item and isinstance(item[0], ip_types):
            yield item[0]


def _call_resolver(resolver: _Resolver, host: str, port: int) -> Any:
    """Call injected resolvers without weakening the default socket contract."""

    attempts = (
        lambda: resolver(host, port, type=socket.SOCK_STREAM),
        lambda: resolver(host, port, socket.SOCK_STREAM),
        lambda: resolver(host, port),
        lambda: resolver(host),
    )
    last_error: TypeError | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise SSRFError(f"host resolution failed for {host!r}")


def consume_limited(chunks: Iterable[bytes], max_bytes: int) -> bytes:
    """Consume byte chunks, stopping immediately when the limit is exceeded."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    parts: list[bytes] = []
    total = 0
    for chunk in chunks:
        if not isinstance(chunk, bytes):
            try:
                chunk = memoryview(chunk).tobytes()
            except (TypeError, ValueError) as exc:
                raise RequestGuardError("response stream yielded a non-byte chunk") from exc
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(
                f"response exceeded response_max_bytes={max_bytes}"
            )
        parts.append(chunk)
    return b"".join(parts)


class RequestGuards:
    """Shared allowlist, SSRF, response, and request-header policy."""

    def __init__(
        self,
        allowed_urls: Iterable[str] | Mapping[str, Any] | str | None = None,
        *,
        allowed_hosts: Iterable[str] | Mapping[str, Any] | str | None = None,
        source_registry: Any = None,
        settings: Mapping[str, Any] | Any | None = None,
        resolver: _Resolver | None = None,
        response_max_bytes: int | None = None,
        request_timeout_seconds: float | None = None,
        max_redirects: int | None = None,
        allowed_mime_types: Iterable[str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        if allowed_urls is None:
            allowed_urls = allowed_hosts
        self._rules = _rules_from(allowed_urls, source_registry)
        self._resolver = resolver if resolver is not None else socket.getaddrinfo

        configured_response_max = (
            response_max_bytes
            if response_max_bytes is not None
            else _setting(settings, "response_max_bytes", DEFAULT_RESPONSE_MAX_BYTES)
        )
        if isinstance(configured_response_max, bool):
            raise ValueError("response_max_bytes must be a positive integer")
        try:
            self.response_max_bytes = min(int(configured_response_max), DEFAULT_RESPONSE_MAX_BYTES)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("response_max_bytes must be a positive integer") from exc

        configured_timeout = (
            request_timeout_seconds
            if request_timeout_seconds is not None
            else _setting(
                settings,
                "request_timeout_seconds",
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
        )
        if isinstance(configured_timeout, bool):
            raise ValueError("request_timeout_seconds must be a positive number")
        try:
            timeout_value = float(configured_timeout)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("request_timeout_seconds must be a positive number") from exc
        if not math.isfinite(timeout_value):
            raise ValueError("request_timeout_seconds must be finite")
        self.request_timeout_seconds = min(
            timeout_value, float(DEFAULT_REQUEST_TIMEOUT_SECONDS)
        )

        configured_redirects = (
            max_redirects
            if max_redirects is not None
            else _setting(settings, "max_redirects", DEFAULT_MAX_REDIRECTS)
        )
        if isinstance(configured_redirects, bool):
            raise ValueError("max_redirects must be a non-negative integer")
        try:
            self.max_redirects = min(int(configured_redirects), DEFAULT_MAX_REDIRECTS)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("max_redirects must be a non-negative integer") from exc

        selected_mime_types = allowed_mime_types
        if selected_mime_types is None:
            selected_mime_types = _setting(
                settings, "allowed_mime_types", DEFAULT_ALLOWED_MIME_TYPES
            )
        if selected_mime_types is None:
            selected_mime_types = ()
        if isinstance(selected_mime_types, str):
            selected_mime_types = (selected_mime_types,)
        self.allowed_mime_types = frozenset(
            str(value).split(";", 1)[0].strip().lower()
            for value in selected_mime_types
            if str(value).strip()
        )
        configured_user_agent = (
            user_agent
            if user_agent is not None
            else _setting(settings, "user_agent", DEFAULT_USER_AGENT)
        )
        if not isinstance(configured_user_agent, str) or not configured_user_agent.strip():
            raise ValueError("user_agent must be a non-empty string")
        self.user_agent = configured_user_agent.strip()
        if self.response_max_bytes < 1:
            raise ValueError("response_max_bytes must be positive")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_redirects < 0:
            raise ValueError("max_redirects must not be negative")

    @classmethod
    def from_source_registry(cls, source_registry: Any, **kwargs: Any) -> "RequestGuards":
        return cls(source_registry=source_registry, **kwargs)

    @property
    def allowed_rules(self) -> tuple[tuple[str | None, str, int | None], ...]:
        return tuple((rule.scheme, rule.host, rule.port) for rule in self._rules)

    def _matches_allowlist(self, target: SplitResult) -> bool:
        host = target.hostname
        if host is None:
            return False
        normalised_host = _normalise_host(host)
        try:
            target_port = target.port
        except ValueError:
            return False
        effective_target_port = (
            target_port
            if target_port is not None
            else _default_port(target.scheme.lower())
        )
        target_scheme = target.scheme.lower()
        for rule in self._rules:
            if rule.host != normalised_host:
                continue
            if rule.scheme is not None and rule.scheme != target_scheme:
                continue
            if rule.port is not None and rule.port != effective_target_port:
                continue
            if rule.port is None and rule.scheme in {"http", "https"}:
                if effective_target_port != _default_port(rule.scheme):
                    continue
            return True
        return False

    def validate_url(self, url: str) -> SplitResult:
        if not isinstance(url, str) or not url.strip():
            raise RequestGuardError("request URL must be a non-empty string")
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
            raise RequestGuardError("request URL contains control characters")
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
        except ValueError as exc:
            raise RequestGuardError(f"invalid request URL: {url!r}") from exc
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https", "fixture"}:
            raise UnsupportedSchemeError(f"unsupported request URL scheme: {parsed.scheme!r}")
        if parsed.username is not None or parsed.password is not None:
            raise RequestGuardError("request URLs must not contain credentials")
        if host is None:
            raise RequestGuardError("request URL must contain a host")
        if not self._matches_allowlist(parsed):
            raise HostNotAllowedError(
                f"request host is outside the Source Registry allowlist: {host}"
            )
        if scheme in {"http", "https"}:
            target_port = parsed.port
            self._validate_resolved_host(
                host,
                target_port if target_port is not None else _default_port(scheme),
            )
        return parsed

    def _validate_resolved_host(self, host: str, port: int | None) -> None:
        try:
            literal = ipaddress.ip_address(host)
        except (TypeError, ValueError):
            literal = None
        if literal is not None:
            self.validate_resolved_address(literal)
            return

        if port is None:
            raise SSRFError(f"could not determine port for host {host!r}")
        try:
            resolver_host = _normalise_host(host)
        except HostNotAllowedError as exc:
            raise SSRFError(f"host resolution failed for {host!r}") from exc
        try:
            resolved = _call_resolver(self._resolver, resolver_host, port)
        except Exception as exc:
            raise SSRFError(f"host resolution failed for {host!r}") from exc

        addresses = list(_iter_resolved_addresses(resolved))
        if not addresses:
            raise SSRFError(f"host resolution returned no addresses for {host!r}")
        for address in addresses:
            self.validate_resolved_address(address)

    def validate_resolved_address(
        self, address: str | ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> None:
        if _is_forbidden_ip(address):
            raise SSRFError(f"resolved address is forbidden: {address}")

    def prepare_headers(self, headers: Mapping[str, str] | None = None) -> dict[str, str]:
        prepared = {
            str(key): str(value)
            for key, value in (headers or {}).items()
            if str(key).lower() != "user-agent"
        }
        prepared["User-Agent"] = self.user_agent
        return prepared

    def validate_mime(self, headers: Mapping[str, Any]) -> str:
        content_type = _header(headers, "content-type")
        mime = content_type.split(";", 1)[0].strip().lower() if content_type else ""
        if mime not in self.allowed_mime_types:
            raise MimeTypeNotAllowedError(
                f"response MIME type is not approved: {content_type or '<missing>'}"
            )
        return mime

    def validate_content_length(self, headers: Mapping[str, Any]) -> None:
        value = _header(headers, "content-length")
        if value is None:
            return
        try:
            content_length = int(value)
        except ValueError as exc:
            raise RequestGuardError("response Content-Length is invalid") from exc
        if content_length < 0:
            raise RequestGuardError("response Content-Length must not be negative")
        if content_length > self.response_max_bytes:
            raise ResponseTooLargeError(
                f"response Content-Length exceeds response_max_bytes={self.response_max_bytes}"
            )

    def redirect_target(self, current_url: str, location: str, redirect_count: int) -> str:
        if isinstance(redirect_count, bool) or not isinstance(redirect_count, int):
            raise RedirectLimitExceededError("redirect count must be a non-negative integer")
        if redirect_count < 0:
            raise RedirectLimitExceededError("redirect count must not be negative")
        if redirect_count >= self.max_redirects:
            raise RedirectLimitExceededError(
                f"response exceeded max_redirects={self.max_redirects}"
            )
        if not isinstance(location, str) or not location.strip():
            raise RequestGuardError("redirect response has an empty Location header")
        target = urljoin(current_url, location.strip())
        self.validate_url(target)
        return target


RequestGuard = RequestGuards
GuardError = RequestGuardError
ResponseSizeError = ResponseTooLargeError
UnsupportedMIMETypeError = MimeTypeNotAllowedError
MIMETypeNotAllowedError = MimeTypeNotAllowedError
RedirectLimitError = RedirectLimitExceededError


__all__ = [
    "DEFAULT_ALLOWED_MIME_TYPES",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_REQUEST_TIMEOUT_SECONDS",
    "DEFAULT_RESPONSE_MAX_BYTES",
    "DEFAULT_USER_AGENT",
    "GuardError",
    "HostNotAllowedError",
    "MIMETypeNotAllowedError",
    "MimeTypeNotAllowedError",
    "RedirectLimitError",
    "RedirectLimitExceededError",
    "RequestGuard",
    "RequestGuardError",
    "RequestGuards",
    "RequestTimeoutError",
    "ResponseSizeError",
    "ResponseTooLargeError",
    "SSRFError",
    "UnsupportedMIMETypeError",
    "UnsupportedSchemeError",
    "consume_limited",
    "is_forbidden_ip",
]
