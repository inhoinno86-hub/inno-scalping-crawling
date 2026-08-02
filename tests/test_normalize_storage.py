from __future__ import annotations

from pathlib import Path

import pytest

from scalping_briefing.normalize.urls import URLNormalizationError, normalize_url
from scalping_briefing.storage.files import (
    LocalFileStorage,
    UnsafeStorageIdentifier,
)


def test_normalize_url_equates_scheme_host_default_port_and_fragment() -> None:
    first = normalize_url(
        "HTTP://EXAMPLE.COM:80/research/brief#section-2"
    )
    second = normalize_url("http://example.com/research/brief")

    assert first == second == "http://example.com/research/brief"


def test_normalize_url_removes_dot_segments_and_collapses_path_separators() -> None:
    assert (
        normalize_url("https://Example.COM:443/a//./b/../%7Ealice/")
        == "https://example.com/a/~alice/"
    )
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("https://example.com:8443") == "https://example.com:8443/"


def test_normalize_url_filters_tracking_and_sorts_meaningful_query_pairs() -> None:
    assert normalize_url(
        "https://example.com/doc?utm_source=news&z=last&fbclid=abc"
        "&page=2&a=hello%20world&utm_campaign=launch&page=1#ignored"
    ) == "https://example.com/doc?a=hello+world&page=1&page=2&z=last"


def test_normalize_url_preserves_meaningful_parameters_with_blank_values() -> None:
    assert normalize_url("https://example.com/doc?flag&ref=meaningful") == (
        "https://example.com/doc?flag=&ref=meaningful"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative/path",
        "https:///missing-host",
        "https://example.com:bad",
        "https://[invalid",
    ],
)
def test_normalize_url_rejects_invalid_absolute_urls(value: str) -> None:
    with pytest.raises(URLNormalizationError):
        normalize_url(value)


def test_local_storage_uses_document_version_paths_and_existing_retention_keys(
    tmp_path: Path,
) -> None:
    settings = {
        "raw_retention_days": 14,
        "normalized_retention_days": "unlimited",
    }
    storage = LocalFileStorage(tmp_path / "storage", settings=settings)

    raw_path = storage.write_raw("version-001", b"<html>raw</html>")
    normalized_path = storage.write_normalized("version-001", "normalized text")

    assert raw_path == tmp_path / "storage" / "raw" / "version-001"
    assert normalized_path == tmp_path / "storage" / "normalized" / "version-001"
    assert storage.read_raw("version-001") == b"<html>raw</html>"
    assert storage.read_normalized("version-001") == b"normalized text"
    assert storage.retention_days("raw") == 14
    assert storage.retention_days("normalized") == "unlimited"
    assert raw_path.parent == tmp_path / "storage" / "raw"
    assert normalized_path.parent == tmp_path / "storage" / "normalized"


@pytest.mark.parametrize(
    "identifier",
    ["", ".", "..", ".hidden", "../outside", "/absolute", "nested/name", "nested\\name"],
)
def test_local_storage_rejects_unsafe_document_version_ids(
    tmp_path: Path, identifier: str
) -> None:
    storage = LocalFileStorage(
        tmp_path / "storage",
        settings={
            "raw_retention_days": 1,
            "normalized_retention_days": 2,
        },
    )

    with pytest.raises(UnsafeStorageIdentifier):
        storage.write_raw(identifier, b"must not escape")

    assert not (tmp_path / "outside").exists()


def test_local_storage_does_not_offer_an_arbitrary_path_namespace(tmp_path: Path) -> None:
    storage = LocalFileStorage(
        tmp_path / "storage",
        settings={
            "raw_retention_days": 1,
            "normalized_retention_days": 2,
        },
    )

    with pytest.raises(ValueError):
        storage.path_for("version-001", "other")  # type: ignore[arg-type]
