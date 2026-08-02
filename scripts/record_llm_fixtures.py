#!/usr/bin/env python3
"""Record JSON fixture responses into a caller-selected mapping file.

The input is a local JSON fixture.  It may describe one record, a list of
records, or a mapping of prompt hashes to records.  No provider client is
used; prompt keys are calculated locally with SHA-256.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_COLLECTION_KEYS = ("records", "fixtures", "entries", "requests", "calls")
_PROMPT_HASH_KEYS = ("prompt_hash", "hash")
_RESPONSE_KEYS = ("response", "output", "completion")
_CONTROL_KEYS = {
    "_mapping_key",
    "_prompt_hash",
    "document_version_id",
    "input_document_version_id",
    "prompt",
    "prompt_hash",
    "hash",
    "recorded_at",
    "recording_version",
}


def prompt_hash(prompt: str) -> str:
    """Return the stable key used by the file-backed fixture client."""

    if not isinstance(prompt, str):
        raise TypeError("fixture prompt must be a string")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON fixture: {path}") from exc


def _mapping_records(payload: Any) -> list[dict[str, Any]]:
    """Normalize supported input shapes into independent record dictionaries."""

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        records: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                raise ValueError("fixture record must be a JSON object")
            records.append(dict(item))
        return records

    if not isinstance(payload, Mapping):
        raise ValueError("input fixture root must be a JSON object or array")

    for collection_key in _COLLECTION_KEYS:
        collection = payload.get(collection_key)
        if collection is None:
            continue
        if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)):
            raise ValueError(f"fixture {collection_key} must be a JSON array")
        shared = {key: value for key, value in payload.items() if key != collection_key}
        records = []
        for item in collection:
            if not isinstance(item, Mapping):
                raise ValueError("fixture record must be a JSON object")
            record = dict(shared)
            record.update(item)
            records.append(record)
        return records

    mappings = payload.get("mappings")
    if isinstance(mappings, Mapping):
        shared = {key: value for key, value in payload.items() if key != "mappings"}
        records = []
        for mapping_key, value in mappings.items():
            if isinstance(value, Mapping):
                record = dict(shared)
                record.update(value)
            else:
                record = dict(shared)
                record["response"] = value
            record["_mapping_key"] = str(mapping_key)
            records.append(record)
        return records

    return [dict(payload)]


def _nested_value(record: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in record:
            return record[name]

    for container_name in (
        "input",
        "document",
        "document_version",
        "input_document",
        "input_document_version",
        "metadata",
    ):
        container = record.get(container_name)
        if isinstance(container, Mapping):
            for name in names:
                if name in container:
                    return container[name]
    return None


def _document_version_id(record: Mapping[str, Any]) -> str:
    value = _nested_value(
        record,
        ("input_document_version_id", "document_version_id", "input_document_version"),
    )
    if isinstance(value, Mapping):
        value = value.get("document_version_id", value.get("id"))
    if value is None or value == "":
        raise ValueError("input fixture must contain document_version_id")
    return str(value)


def _recorded_at(record: Mapping[str, Any], override: str | None) -> str:
    value = override if override is not None else _nested_value(record, ("recorded_at",))
    if value is None or value == "":
        return _utc_now()
    return str(value)


def _prompt_key(record: Mapping[str, Any]) -> str:
    prompt = _nested_value(record, ("prompt",))
    supplied_hash = _nested_value(record, _PROMPT_HASH_KEYS)
    mapping_key = record.get("_mapping_key")

    if prompt is not None:
        digest = prompt_hash(prompt)
        if supplied_hash is not None and str(supplied_hash) != digest:
            raise ValueError("input fixture prompt_hash does not match prompt")
        return digest

    for value in (supplied_hash, mapping_key):
        if value is not None and value != "":
            return str(value)
    raise ValueError("input fixture must contain prompt or prompt_hash")


def _response(record: Mapping[str, Any]) -> tuple[bool, Any]:
    for key in _RESPONSE_KEYS:
        if key in record:
            return True, record[key]
    return False, None


def _entry(record: Mapping[str, Any], *, recorded_at: str | None) -> dict[str, Any]:
    has_response, response = _response(record)
    if not has_response:
        raise ValueError("input fixture must contain response, output, or completion")

    entry: dict[str, Any] = {
        "recorded_at": _recorded_at(record, recorded_at),
        "input_document_version_id": _document_version_id(record),
    }
    for key, value in record.items():
        if key in _CONTROL_KEYS or key in _RESPONSE_KEYS:
            continue
        entry[key] = value
    entry["response"] = response
    return entry


def _load_destination(path: Path, *, default_recorded_at: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "recording_version": 1,
            "recorded_at": default_recorded_at,
            "mappings": {},
        }

    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError("mapping file root must be a JSON object")
    if "recording_version" not in payload or "recorded_at" not in payload or "mappings" not in payload:
        raise ValueError("mapping file must contain recording_version, recorded_at, and mappings")
    if not isinstance(payload["mappings"], dict):
        raise ValueError("mapping file mappings must be a JSON object")
    return payload


def record_fixture(
    input_fixture: str | Path | Mapping[str, Any],
    mapping_path: str | Path,
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Record local fixture input and return the updated mapping payload."""

    source = _read_json(Path(input_fixture)) if isinstance(input_fixture, (str, Path)) else input_fixture
    records = _mapping_records(source)
    if not records:
        raise ValueError("input fixture contains no records")

    destination_path = Path(mapping_path)
    first_recorded_at = _recorded_at(records[0], recorded_at)
    destination = _load_destination(destination_path, default_recorded_at=first_recorded_at)
    mappings = destination["mappings"]
    for record in records:
        mappings[_prompt_key(record)] = _entry(record, recorded_at=recorded_at)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_text(
        json.dumps(destination, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


record_fixtures = record_fixture
update_mapping = record_fixture


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_fixture", nargs="?", type=Path)
    parser.add_argument("mapping_file", nargs="?", type=Path)
    parser.add_argument(
        "--input",
        "--input-path",
        "--input-fixture",
        dest="input_option",
        type=Path,
    )
    parser.add_argument(
        "--mapping",
        "--map",
        "--mapping-file",
        "--mapping-path",
        "--output",
        "-m",
        dest="mapping_option",
        type=Path,
    )
    parser.add_argument(
        "--recorded-at",
        help="Use this timestamp when input fixture does not provide one.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_fixture = args.input_option or args.input_fixture
    mapping_path = args.mapping_option or args.mapping_file
    if input_fixture is None or mapping_path is None:
        _parser().error("input fixture and mapping file are required")
    record_fixture(input_fixture, mapping_path, recorded_at=args.recorded_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
