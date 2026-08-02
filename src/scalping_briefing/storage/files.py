"""Local raw and normalized document-version storage.

Only two local namespaces are exposed: ``storage/raw`` and
``storage/normalized``.  Object-store support is intentionally represented by
a protocol so a future implementation can be added without adding a client,
network behavior, or credential handling to this phase.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from os import PathLike
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, runtime_checkable
from uuid import uuid4


StorageKind: TypeAlias = Literal["raw", "normalized"]
StorageContent: TypeAlias = str | bytes | bytearray | memoryview

DEFAULT_STORAGE_ROOT = Path("storage")
RAW_DIRECTORY = "raw"
NORMALIZED_DIRECTORY = "normalized"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class StorageError(RuntimeError):
    """Raised when local storage cannot safely complete an operation."""


class UnsafeStorageIdentifier(ValueError):
    """Raised when a document-version identifier is not a safe path segment."""


class StorageConfigurationError(ValueError):
    """Raised when the existing retention configuration is unavailable/invalid."""


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal future object-store boundary; deliberately has no implementation."""

    def put(self, key: str, content: bytes) -> None:
        """Store bytes at a provider-specific object key."""

    def get(self, key: str) -> bytes:
        """Read bytes from a provider-specific object key."""

    def delete(self, key: str) -> None:
        """Delete a provider-specific object key."""


StorageBackend = ObjectStore
ObjectStorage = ObjectStore


def _config_value(config: Any, key: str) -> Any:
    if isinstance(config, Mapping):
        try:
            return config[key]
        except KeyError as exc:
            raise StorageConfigurationError(
                f"configuration is missing existing key: {key}"
            ) from exc

    try:
        return getattr(config, key)
    except AttributeError as exc:
        raise StorageConfigurationError(
            f"configuration is missing existing key: {key}"
        ) from exc


def _load_default_config() -> Any:
    # Import lazily so the storage module remains usable with a supplied
    # settings object and does not create a new configuration contract.
    from scalping_briefing.config import load_config

    return load_config(environ={})


def _validate_retention_value(key: str, value: Any) -> int | Literal["unlimited"]:
    if key == "normalized_retention_days" and value == "unlimited":
        return value
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        expected = (
            "an integer or 'unlimited'"
            if key.startswith("normalized")
            else "a non-negative integer"
        )
        raise StorageConfigurationError(f"{key} must be {expected}")
    return value


def validate_document_version_id(document_version_id: str) -> str:
    """Validate and return one safe, single path-segment identifier."""

    if not isinstance(document_version_id, str):
        raise UnsafeStorageIdentifier("document_version_id must be a string")
    if (
        not document_version_id
        or document_version_id in {".", ".."}
        or "/" in document_version_id
        or "\\" in document_version_id
        or "\x00" in document_version_id
        or not _SAFE_IDENTIFIER.fullmatch(document_version_id)
    ):
        raise UnsafeStorageIdentifier(
            "document_version_id must be a safe non-empty path segment"
        )
    return document_version_id


class LocalFileStorage:
    """Store document-version bytes below fixed raw and normalized directories."""

    def __init__(
        self,
        root: str | PathLike[str] = DEFAULT_STORAGE_ROOT,
        config: Any | None = None,
        *,
        settings: Any | None = None,
        storage_root: str | PathLike[str] | None = None,
    ) -> None:
        if config is not None and settings is not None:
            raise TypeError("use config or settings, not both")
        if storage_root is not None:
            if Path(root) != DEFAULT_STORAGE_ROOT:
                raise TypeError("use root or storage_root, not both")
            root = storage_root

        selected_config = config if config is not None else settings
        if selected_config is None:
            selected_config = _load_default_config()

        self.root = Path(root)
        self.raw_root = self.root / RAW_DIRECTORY
        self.normalized_root = self.root / NORMALIZED_DIRECTORY
        self.raw_retention_days = _validate_retention_value(
            "raw_retention_days",
            _config_value(selected_config, "raw_retention_days"),
        )
        self.normalized_retention_days = _validate_retention_value(
            "normalized_retention_days",
            _config_value(selected_config, "normalized_retention_days"),
        )

        if self.root.is_symlink():
            raise StorageError(f"storage root must not be a symlink: {self.root}")
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (self.raw_root, self.normalized_root):
            if directory.is_symlink():
                raise StorageError(
                    f"storage directory must not be a symlink: {directory}"
                )
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def raw_directory(self) -> Path:
        """Return the local raw namespace directory."""

        return self.raw_root

    @property
    def normalized_directory(self) -> Path:
        """Return the local normalized namespace directory."""

        return self.normalized_root

    def retention_days(self, kind: StorageKind) -> int | Literal["unlimited"]:
        """Return the configured retention for one fixed namespace."""

        if kind == "raw":
            return self.raw_retention_days
        if kind == "normalized":
            return self.normalized_retention_days
        raise ValueError("storage kind must be 'raw' or 'normalized'")

    def _directory(self, kind: StorageKind) -> Path:
        if kind == "raw":
            return self.raw_root
        if kind == "normalized":
            return self.normalized_root
        raise ValueError("storage kind must be 'raw' or 'normalized'")

    def _path(self, kind: StorageKind, document_version_id: str) -> Path:
        identifier = validate_document_version_id(document_version_id)
        directory = self._directory(kind)
        if directory.is_symlink():
            raise StorageError(f"storage directory must not be a symlink: {directory}")

        directory_resolved = directory.resolve(strict=False)
        candidate = directory / identifier
        candidate_resolved = candidate.resolve(strict=False)
        try:
            candidate_resolved.relative_to(directory_resolved)
        except ValueError as exc:
            raise StorageError("storage path escaped its fixed namespace") from exc
        return candidate

    def raw_path(self, document_version_id: str) -> Path:
        """Return the path for one raw document version."""

        return self._path("raw", document_version_id)

    def normalized_path(self, document_version_id: str) -> Path:
        """Return the path for one normalized document version."""

        return self._path("normalized", document_version_id)

    def path_for(self, document_version_id: str, kind: StorageKind = "raw") -> Path:
        """Return a path in one of the two fixed namespaces."""

        return self._path(kind, document_version_id)

    def _write_path(self, path: Path, content: StorageContent) -> Path:
        if isinstance(content, str):
            payload = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray, memoryview)):
            payload = bytes(content)
        else:
            raise TypeError("stored content must be text or bytes-like")

        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def write_raw(self, document_version_id: str, content: StorageContent) -> Path:
        """Atomically write raw bytes/text for a document version."""

        return self._write_path(self.raw_path(document_version_id), content)

    def write_normalized(
        self, document_version_id: str, content: StorageContent
    ) -> Path:
        """Atomically write normalized bytes/text for a document version."""

        return self._write_path(self.normalized_path(document_version_id), content)

    def write(
        self,
        kind: StorageKind,
        document_version_id: str,
        content: StorageContent,
    ) -> Path:
        """Write content to one of the fixed local namespaces."""

        return self._write_path(self._path(kind, document_version_id), content)

    def read_raw(self, document_version_id: str) -> bytes:
        """Read raw content for a document version."""

        return self.raw_path(document_version_id).read_bytes()

    def read_normalized(self, document_version_id: str) -> bytes:
        """Read normalized content for a document version."""

        return self.normalized_path(document_version_id).read_bytes()

    def read(self, kind: StorageKind, document_version_id: str) -> bytes:
        """Read content from one of the fixed local namespaces."""

        return self._path(kind, document_version_id).read_bytes()

    def exists(self, kind: StorageKind, document_version_id: str) -> bool:
        """Return whether a safe version path exists in one namespace."""

        return self._path(kind, document_version_id).is_file()


FileStorage = LocalFileStorage
LocalStorage = LocalFileStorage


__all__ = [
    "DEFAULT_STORAGE_ROOT",
    "FileStorage",
    "LocalFileStorage",
    "LocalStorage",
    "NORMALIZED_DIRECTORY",
    "ObjectStorage",
    "ObjectStore",
    "RAW_DIRECTORY",
    "StorageBackend",
    "StorageConfigurationError",
    "StorageError",
    "StorageKind",
    "UnsafeStorageIdentifier",
    "validate_document_version_id",
]
