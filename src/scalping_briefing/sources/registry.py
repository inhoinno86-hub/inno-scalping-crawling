"""Policy-driven source registry and shared connector result contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from scalping_briefing.pipeline.source_policy import (
    DEFAULT_SOURCE_POLICY,
    DEFAULT_SOURCE_SCHEMA,
    SourcePolicyError,
    load_source_policy,
)
from scalping_briefing.sources.window import calculate_collection_window


class SourceRegistryError(ValueError):
    """Base class for source selection and collection errors."""


class SourceInactiveError(SourceRegistryError):
    """Raised before any request is made for an inactive source."""


# Compatibility names make the policy gate explicit to callers without making
# connector selection depend on source ``type`` or ``fixture`` values.
InactiveSourceError = SourceInactiveError
SourceNotActiveError = SourceInactiveError


class ConnectorError(SourceRegistryError):
    """Raised when a connector cannot turn a response into source items."""


class CursorState(dict[str, Any]):
    """Case-tolerant cursor mapping with stable lower-case serialisation."""

    _ALIASES = {
        "etag": "etag",
        "if-none-match": "etag",
        "last-modified": "last_modified",
        "last_modified": "last_modified",
        "if-modified-since": "last_modified",
    }

    @classmethod
    def _key(cls, key: object) -> object:
        if not isinstance(key, str):
            return key
        return cls._ALIASES.get(key.strip().lower(), key)

    def __init__(self, values: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__()
        for key, value in dict(values or {}, **kwargs).items():
            super().__setitem__(self._key(key), value)

    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(self._key(key))

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(self._key(key), value)

    def __contains__(self, key: object) -> bool:
        return super().__contains__(self._key(key))

    def get(self, key: str, default: Any = None) -> Any:
        return super().get(self._key(key), default)

    def pop(self, key: str, *args: Any) -> Any:
        return super().pop(self._key(key), *args)

    def copy(self) -> "CursorState":
        return CursorState(self)


class ItemRecord(dict[str, Any]):
    """JSON-compatible item with both mapping and attribute access."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        return dict(self)


@dataclass
class ConnectorResult:
    """Common result returned by every source connector."""

    source_id: str
    items: list[ItemRecord] = field(default_factory=list)
    cursor: CursorState | None = None
    status_code: int = 200
    not_modified: bool = False
    window: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None

    def __post_init__(self) -> None:
        self.items = [
            item if isinstance(item, ItemRecord) else ItemRecord(item)
            for item in self.items
        ]
        if self.cursor is not None and not isinstance(self.cursor, CursorState):
            self.cursor = CursorState(self.cursor)

    @property
    def entries(self) -> list[ItemRecord]:
        return self.items

    @property
    def records(self) -> list[ItemRecord]:
        return self.items

    @property
    def response_status(self) -> int:
        return self.status_code

    @property
    def new_version(self) -> bool:
        # A successful 200 response is a new source version even when the
        # response contains no entries.  304 is the only conditional-response
        # status that explicitly means "no new version".
        return 200 <= self.status_code < 300 and not self.not_modified

    @property
    def has_new_version(self) -> bool:
        return self.new_version

    @property
    def changed(self) -> bool:
        return self.new_version

    @property
    def truncated(self) -> bool:
        return bool(self.window is not None and self.window.truncated)

    @property
    def duplicate_count(self) -> int:
        return int(self.metadata.get("duplicate_count", 0))

    @property
    def deduplicated_count(self) -> int:
        return self.duplicate_count

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source_id": self.source_id,
            "items": [item.as_dict() for item in self.items],
            "cursor": dict(self.cursor) if self.cursor is not None else None,
            "status_code": self.status_code,
            "not_modified": self.not_modified,
            "metadata": dict(self.metadata),
        }
        if self.window is not None:
            payload["window"] = self.window.as_dict()
        if self.content_hash is not None:
            payload["content_hash"] = self.content_hash
        return payload

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


class Connector(Protocol):
    def collect(self, **kwargs: Any) -> ConnectorResult:
        ...


class SourceRecord(MutableMapping[str, Any]):
    """Mutable policy record exposed with mapping and attribute semantics."""

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._values[key] = value

    def __delitem__(self, key: str) -> None:
        del self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    to_dict = as_dict


def source_value(source: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def source_id(source: Mapping[str, Any] | Any) -> str:
    value = source_value(source, "source_id")
    if not isinstance(value, str) or not value:
        raise SourceRegistryError("source record requires a non-empty source_id")
    return value


def source_metadata(source: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    value = source_value(source, "metadata", {})
    return value if isinstance(value, Mapping) else {}


def coerce_cursor(value: Any) -> CursorState | None:
    if value is None:
        return None
    if isinstance(value, CursorState):
        return value.copy()
    if isinstance(value, Mapping):
        return CursorState(value)
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return CursorState({"value": text})
            if isinstance(parsed, Mapping):
                return CursorState(parsed)
            return CursorState({"value": parsed})
    raise ConnectorError("cursor must be a mapping, JSON object, string, or null")


def conditional_headers(cursor: Any) -> dict[str, str]:
    """Build HTTP conditional headers from a feed cursor."""

    state = coerce_cursor(cursor)
    if state is None:
        return {}
    headers: dict[str, str] = {}
    etag = state.get("etag")
    if etag is not None and str(etag).strip():
        headers["If-None-Match"] = str(etag)
    last_modified = state.get("last_modified")
    if last_modified is not None and str(last_modified).strip():
        headers["If-Modified-Since"] = str(last_modified)
    return headers


def response_header(headers: Mapping[str, Any], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def response_status(response: Any) -> int:
    try:
        return int(getattr(response, "status_code"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ConnectorError("transport response requires integer status_code") from exc


def response_headers(response: Any) -> Mapping[str, Any]:
    headers = getattr(response, "headers", {})
    if isinstance(headers, Mapping):
        return headers
    if headers is None:
        return {}
    try:
        return dict(headers)
    except (TypeError, ValueError) as exc:
        raise ConnectorError("transport response headers must be a mapping") from exc


def response_body(response: Any) -> bytes:
    body = getattr(response, "content", None)
    if body is None:
        body = getattr(response, "body", None)
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    if body is not None:
        try:
            return bytes(body)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("transport response body must be bytes") from exc
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text.encode("utf-8")
    raise ConnectorError("transport response does not expose a body")


def response_cursor(response: Any, previous: Any = None) -> CursorState | None:
    state = coerce_cursor(previous)
    headers = response_headers(response)
    etag = response_header(headers, "etag")
    last_modified = response_header(headers, "last-modified")
    if etag is not None or last_modified is not None:
        state = state or CursorState()
        if etag is not None:
            state["etag"] = etag
        if last_modified is not None:
            state["last_modified"] = last_modified
    return state


def update_source_cursor(source: Mapping[str, Any] | Any, cursor: Any) -> None:
    if cursor is None:
        return
    if isinstance(source, MutableMapping):
        source["cursor"] = cursor
        return
    try:
        setattr(source, "cursor", cursor)
    except (AttributeError, TypeError):
        return


def settings_value(settings: Mapping[str, Any] | Any | None, key: str, default: Any) -> Any:
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        return settings.get(key, default)
    return getattr(settings, key, default)


class SourceRegistry:
    """Load policy records and select one connector by ``connector_type``."""

    _CONNECTOR_TYPES = {"rss", "atom", "paper_metadata", "github_api", "html"}

    def __init__(
        self,
        policy: Mapping[str, Any] | str | None = None,
        *,
        source_policy: Mapping[str, Any] | None = None,
        policy_path: str | None = None,
        schema_path: str | None = None,
        transport: Any = None,
        transport_factory: Callable[[SourceRecord], Any] | Mapping[str, Any] | None = None,
        settings: Mapping[str, Any] | Any | None = None,
    ) -> None:
        if source_policy is not None:
            if policy is not None:
                raise TypeError("use policy or source_policy, not both")
            policy = source_policy
        if isinstance(policy, str):
            if policy_path is not None:
                raise TypeError("use policy path or policy_path, not both")
            policy_path = policy
            policy = None
        if policy is None:
            policy = load_source_policy(
                policy_path or DEFAULT_SOURCE_POLICY,
                schema_path=schema_path or DEFAULT_SOURCE_SCHEMA,
            )
        if not isinstance(policy, Mapping):
            raise SourcePolicyError("source policy root must be a mapping")
        raw_sources = policy.get("sources")
        if not isinstance(raw_sources, list):
            raise SourcePolicyError("source policy must contain a sources list")
        records: dict[str, SourceRecord] = {}
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise SourcePolicyError("each source policy entry must be a mapping")
            record = SourceRecord(raw_source)
            identifier = source_id(record)
            if identifier in records:
                raise SourcePolicyError(f"duplicate source_id: {identifier}")
            records[identifier] = record
        self.policy = dict(policy)
        self.source_policy = self.policy
        self.sources = records
        self.transport = transport
        self.transport_factory = transport_factory
        self.settings = settings
        self._transports: dict[str, Any] = {}

    @classmethod
    def from_policy(cls, policy: Mapping[str, Any], **kwargs: Any) -> "SourceRegistry":
        return cls(policy, **kwargs)

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(self.sources)

    @property
    def active_sources(self) -> tuple[SourceRecord, ...]:
        return tuple(source for source in self.sources.values() if source.get("active") is True)

    def __iter__(self):
        return iter(self.sources)

    def __len__(self) -> int:
        return len(self.sources)

    def __getitem__(self, source_id_value: str) -> SourceRecord:
        return self.get_source(source_id_value)

    def get_source(self, source_id_value: str) -> SourceRecord:
        try:
            return self.sources[source_id_value]
        except KeyError as exc:
            raise SourceRegistryError(f"unknown source_id: {source_id_value}") from exc

    source = get_source

    def load(self, source_id_value: str) -> SourceRecord:
        """Return one policy record through the registry interface."""

        return self.get_source(source_id_value)

    def _connector_class(self, source: SourceRecord) -> type[Any]:
        connector_type = str(source.get("connector_type", "")).strip().lower()
        if connector_type in {"rss", "rss_2_0", "atom", "feed", "feed_xml"}:
            from scalping_briefing.sources.connectors.rss_atom import RSSAtomConnector

            return RSSAtomConnector
        if connector_type in {
            "paper_metadata",
            "json_metadata",
            "json_meta",
        }:
            from scalping_briefing.sources.connectors.json_meta import JSONMetadataConnector

            return JSONMetadataConnector
        if connector_type in {"github", "github_api"}:
            from scalping_briefing.sources.connectors.github import GitHubConnector

            return GitHubConnector
        if connector_type in {"html", "html_docs", "html_document"}:
            from scalping_briefing.sources.connectors.html_docs import HTMLDocumentConnector

            return HTMLDocumentConnector
        raise SourceRegistryError(
            f"unsupported connector_type for {source.source_id}: {connector_type!r}"
        )

    def _transport_for(self, source: SourceRecord) -> Any:
        if self.transport is not None:
            return self.transport
        if self.transport_factory is not None:
            if isinstance(self.transport_factory, Mapping):
                factory = self.transport_factory.get(source.source_id)
                if factory is None:
                    factory = self.transport_factory.get("default")
            else:
                factory = self.transport_factory
            if factory is None:
                raise SourceRegistryError(f"no transport configured for {source.source_id}")
            return factory(source) if callable(factory) else factory
        if source.source_id in self._transports:
            return self._transports[source.source_id]

        from scalping_briefing.net.transport import FixtureTransport, HTTPTransport

        scheme = urlsplit(str(source.get("base_url", ""))).scheme.lower()
        if scheme == "fixture":
            selected = FixtureTransport(source_registry=self.policy, settings=self.settings)
        else:
            selected = HTTPTransport(source_registry=self.policy, settings=self.settings)
        self._transports[source.source_id] = selected
        return selected

    def connector_for(
        self,
        source_id_value: str,
        *,
        transport: Any = None,
        cursor: Any = None,
    ) -> Connector:
        source = self.get_source(source_id_value)
        connector_class = self._connector_class(source)
        return connector_class(
            source,
            transport if transport is not None else self._transport_for(source),
            settings=self.settings,
            cursor=source.get("cursor") if cursor is None else cursor,
        )

    get_connector = connector_for
    select_connector = connector_for
    connector = connector_for

    def collect(
        self,
        source_id_value: str,
        *,
        transport: Any = None,
        cursor: Any = None,
        **kwargs: Any,
    ) -> ConnectorResult:
        source = self.get_source(source_id_value)
        if source.get("active") is not True:
            raise SourceInactiveError(
                f"source collection rejected because active is false: {source_id_value}"
            )
        connector = self.connector_for(
            source_id_value,
            transport=transport,
            cursor=source.get("cursor") if cursor is None else cursor,
        )
        effective_cursor = source.get("cursor") if cursor is None else cursor
        result = connector.collect(cursor=effective_cursor, **kwargs)
        update_source_cursor(source, result.cursor)
        return result

    collect_source = collect
    collect_one = collect

    def close(self) -> None:
        transports: list[Any] = []
        for candidate in self._transports.values():
            if all(candidate is not existing for existing in transports):
                transports.append(candidate)
        if self.transport is not None and all(
            self.transport is not existing for existing in transports
        ):
            transports.append(self.transport)
        for transport in transports:
            close = getattr(transport, "close", None)
            if close is not None:
                close()

    def __enter__(self) -> "SourceRegistry":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


__all__ = [
    "Connector",
    "ConnectorError",
    "ConnectorResult",
    "CursorState",
    "InactiveSourceError",
    "ItemRecord",
    "SourceInactiveError",
    "SourceNotActiveError",
    "SourceRecord",
    "SourceRegistry",
    "SourceRegistryError",
    "coerce_cursor",
    "calculate_collection_window",
    "conditional_headers",
    "response_body",
    "response_cursor",
    "response_header",
    "response_headers",
    "response_status",
    "settings_value",
    "source_id",
    "source_metadata",
    "source_value",
    "update_source_cursor",
]
