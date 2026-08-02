"""Deterministic canonical URL normalization.

The normalizer is deliberately limited to URL spelling.  It does not resolve
URLs, follow redirects, make requests, or infer that two different resources
are the same document beyond their normalized URL spelling.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import SplitResult, parse_qsl, quote, urlencode, urlsplit, urlunsplit


class URLNormalizationError(ValueError):
    """Raised when a URL cannot be normalized safely."""


_HEX_ESCAPE = re.compile(r"%([0-9A-Fa-f]{2})")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*$")
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_DEFAULT_PORTS = {
    "ftp": 21,
    "http": 80,
    "https": 443,
    "ws": 80,
    "wss": 443,
}

# These parameters are identifiers added by widely used analytics and
# campaign systems.  Parameters not in this set remain meaningful data and
# are preserved, even when their names are unfamiliar.
_TRACKING_PARAMETER_NAMES = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gclid",
        "igshid",
        "li_fat_id",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "s_cid",
        "ttclid",
        "twclid",
        "yclid",
    }
)


def _normalize_percent_encoding(value: str) -> str:
    """Normalize percent escapes and encode stray percent characters.

    Percent-encoded unreserved characters have the same URL meaning as their
    literal spelling, while reserved characters must remain escaped.  Escape
    hex digits are emitted in uppercase so equivalent spellings compare equal.
    """

    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%":
            match = _HEX_ESCAPE.match(value, index)
            if match is None:
                output.append("%25")
                index += 1
                continue
            decoded = chr(int(match.group(1), 16))
            if decoded in _UNRESERVED:
                output.append(decoded)
            else:
                output.append(f"%{match.group(1).upper()}")
            index = match.end()
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _normalize_path(path: str) -> str:
    """Remove dot segments and repeated separators without changing a suffix."""

    normalized = _normalize_percent_encoding(path or "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized

    had_trailing_slash = normalized.endswith("/")
    segments: list[str] = []
    for segment in normalized.split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment)

    result = "/" + "/".join(segments)
    if result == "":
        result = "/"
    if had_trailing_slash and result != "/":
        result += "/"
    return result


def _normalize_host(parts: SplitResult) -> str:
    """Return a lower-case host with only a meaningful port retained."""

    try:
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise URLNormalizationError("URL contains an invalid port") from exc

    if not hostname:
        raise URLNormalizationError("URL must include a host")

    if ":" in hostname:
        # ``hostname`` omits IPv6 brackets.  Zone identifiers, when present,
        # are already part of the hostname and remain percent-encoded.
        host = hostname.lower()
        host = f"[{host}]"
    else:
        try:
            host = hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise URLNormalizationError("URL contains an invalid host") from exc

    default_port = _DEFAULT_PORTS.get(parts.scheme.lower())
    if port is not None and port != default_port:
        host = f"{host}:{port}"

    # Credentials are not part of the host, and preserving their original
    # spelling is important because usernames/passwords can be case-sensitive.
    # Keep the raw user-info segment if one was supplied; no network operation
    # is performed by this module.
    if "@" in parts.netloc:
        user_info = parts.netloc.rsplit("@", 1)[0]
        host = f"{user_info}@{host}"
    return host


def _is_tracking_parameter(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("utm_") or lowered in _TRACKING_PARAMETER_NAMES


def _normalize_query(query: str) -> str:
    if not query:
        return ""

    pairs = [
        (name, value)
        for name, value in parse_qsl(query, keep_blank_values=True)
        if not _is_tracking_parameter(name)
    ]
    pairs.sort(key=lambda pair: pair[0:2])
    return urlencode(pairs, doseq=True)


def normalize_url(url: str) -> str:
    """Return a deterministic canonical spelling of an absolute URL.

    Scheme and host are lower-cased, HTTP(S) default ports are removed, dot
    segments and repeated path separators are normalized, known tracking
    parameters are discarded, meaningful query pairs are sorted, and the
    fragment is removed.  The input must have a scheme and host; it is never
    fetched or otherwise resolved.
    """

    if not isinstance(url, str) or not url.strip():
        raise URLNormalizationError("URL must be a non-empty string")

    candidate = url.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise URLNormalizationError("URL is malformed") from exc
    scheme = parts.scheme.lower()
    if not scheme or not _SCHEME.fullmatch(scheme):
        raise URLNormalizationError("URL must include a valid scheme")
    if not parts.netloc:
        raise URLNormalizationError("URL must include a host")

    host = _normalize_host(parts)
    path = _normalize_path(parts.path)
    # ``quote`` protects raw spaces and other illegal characters while leaving
    # valid path delimiters and already-normalized percent escapes untouched.
    path = quote(
        path,
        safe="/%:@!$&'()*+,;=-._~%",
    )
    query = _normalize_query(parts.query)
    return urlunsplit((scheme, host, path, query, ""))


def canonicalize_url(url: str) -> str:
    """Compatibility spelling for :func:`normalize_url`."""

    return normalize_url(url)


def canonical_url(url: str) -> str:
    """Compatibility spelling for :func:`normalize_url`."""

    return normalize_url(url)


def normalize_urls(urls: Iterable[str]) -> list[str]:
    """Normalize a sequence while retaining input order."""

    return [normalize_url(url) for url in urls]


__all__ = [
    "URLNormalizationError",
    "canonical_url",
    "canonicalize_url",
    "normalize_url",
    "normalize_urls",
]
