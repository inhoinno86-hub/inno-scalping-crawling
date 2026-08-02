"""Deterministic, standard-library HTML sanitization.

External documents are data, not markup that the application should trust.
This module keeps ordinary document structure and text while removing the
small set of executable HTML vectors required by the Phase 1 contract.
"""

from __future__ import annotations

from html import escape, unescape
from html.parser import HTMLParser
import re
from typing import Final


_DROP_ELEMENTS: Final[frozenset[str]] = frozenset({"script", "iframe", "object"})
_VOID_ELEMENTS: Final[frozenset[str]] = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_URL_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    {
        "action",
        "background",
        "cite",
        "formaction",
        "href",
        "icon",
        "longdesc",
        "manifest",
        "ping",
        "poster",
        "src",
        "srcset",
        "usemap",
        "xlink:href",
    }
)
_UNSAFE_SCHEME: Final[re.Pattern[str]] = re.compile(
    r"(?:javascript|data)\s*:", re.IGNORECASE
)
_UNSAFE_CSS_URL: Final[re.Pattern[str]] = re.compile(
    r"url\s*\(\s*['\"]?\s*(?:javascript|data)\s*:",
    re.IGNORECASE,
)


def _normalise_url_value(value: str) -> str:
    """Apply browser-like control-character handling before scheme checks."""

    # Leading controls and embedded line breaks are ignored by URL parsers in
    # browsers.  Removing them before the check makes this guard fail closed
    # for obfuscated ``java\nscript:`` and ``data:`` values too.
    return re.sub(r"[\x00-\x20]+", "", unescape(value)).lower()


def _unsafe_url(value: str | None, *, srcset: bool = False) -> bool:
    if value is None:
        return True
    normalised = _normalise_url_value(value)
    if _UNSAFE_SCHEME.match(normalised):
        return True
    if srcset:
        # A srcset can contain several comma-separated candidates.  The
        # normalised value still retains commas, so a scheme after a comma is
        # unsafe even when the first candidate is ordinary.
        return bool(re.search(r"(?:^|,)\s*(?:javascript|data)\s*:", normalised))
    return False


def _unsafe_style(value: str | None) -> bool:
    if value is None:
        return False
    return _UNSAFE_CSS_URL.search(unescape(value)) is not None


class _SanitizingParser(HTMLParser):
    """HTML parser that serializes only inert, allowlisted structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dropped_depth = 0

    def _attributes(self, attrs: list[tuple[str, str | None]]) -> str:
        safe: list[str] = []
        for name, value in attrs:
            lowered = name.lower()
            if lowered.startswith("on"):
                continue
            if lowered in _URL_ATTRIBUTES and _unsafe_url(
                value, srcset=lowered == "srcset"
            ):
                continue
            if lowered == "style" and _unsafe_style(value):
                continue
            escaped_name = escape(lowered, quote=True)
            if value is None:
                safe.append(f" {escaped_name}")
            else:
                safe.append(f' {escaped_name}="{escape(value, quote=True)}"')
        return "".join(safe)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if self._dropped_depth:
            if lowered in _DROP_ELEMENTS:
                self._dropped_depth += 1
            return
        if lowered in _DROP_ELEMENTS:
            self._dropped_depth = 1
            return
        self.parts.append(f"<{lowered}{self._attributes(attrs)}>")

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        lowered = tag.lower()
        if self._dropped_depth or lowered in _DROP_ELEMENTS:
            return
        self.parts.append(f"<{lowered}{self._attributes(attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if self._dropped_depth:
            if lowered in _DROP_ELEMENTS:
                self._dropped_depth -= 1
            return
        if lowered not in _VOID_ELEMENTS:
            self.parts.append(f"</{lowered}>")

    def handle_data(self, data: str) -> None:
        if not self._dropped_depth:
            self.parts.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if not self._dropped_depth:
            self.parts.append(escape(unescape(f"&{name};"), quote=False))

    def handle_charref(self, name: str) -> None:
        if not self._dropped_depth:
            self.parts.append(escape(unescape(f"&#{name};"), quote=False))

    def handle_comment(self, _data: str) -> None:
        # Comments are not useful publication content and can hide confusing
        # instructions from a human reviewer.  Drop them at the trust boundary.
        return

    def handle_decl(self, _decl: str) -> None:
        # Doctype declarations are not needed in normalized source fragments.
        return


def sanitize_html(value: str | bytes) -> str:
    """Return an inert HTML fragment with executable vectors removed.

    ``script``, ``iframe``, and ``object`` elements are removed together with
    their contents.  Event-handler attributes and ``javascript:``/``data:``
    URL attributes are omitted.  Ordinary text is escaped and retained, so a
    prompt-injection sentence remains visible as text and cannot become an
    application instruction.
    """

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        raise TypeError("HTML content must be a string or bytes")

    parser = _SanitizingParser()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def sanitize(value: str | bytes) -> str:
    """Compatibility alias for :func:`sanitize_html`."""

    return sanitize_html(value)


sanitize_html_fragment = sanitize_html
sanitize_document = sanitize_html


__all__ = [
    "sanitize",
    "sanitize_document",
    "sanitize_html",
    "sanitize_html_fragment",
]
