from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from scripts.record_llm_fixtures import record_fixture


ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "scripts" / "record_llm_fixtures.py"


def test_recorder_preserves_mapping_envelope_and_records_input_metadata(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    mapping_path = tmp_path / "selected-map.json"
    prompt = "offline recorder prompt"
    input_path.write_text(
        json.dumps(
            {
                "recorded_at": "2026-08-03T01:02:03Z",
                "document_version_id": "document-version-42",
                "prompt": prompt,
                "response": {"ok": True},
                "model_name": "fixture",
            }
        ),
        encoding="utf-8",
    )
    mapping_path.write_text(
        json.dumps(
            {
                "recording_version": 9,
                "recorded_at": "2026-08-01T00:00:00Z",
                "mappings": {"existing": {"response": {"old": True}}},
            }
        ),
        encoding="utf-8",
    )

    record_fixture(input_path, mapping_path)
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    assert payload["recording_version"] == 9
    assert payload["recorded_at"] == "2026-08-01T00:00:00Z"
    assert set(payload["mappings"]) == {"existing", digest}
    assert payload["mappings"][digest] == {
        "recorded_at": "2026-08-03T01:02:03Z",
        "input_document_version_id": "document-version-42",
        "model_name": "fixture",
        "response": {"ok": True},
    }


def test_recorder_updates_caller_selected_file_for_multiple_offline_records(tmp_path: Path) -> None:
    input_path = tmp_path / "records.json"
    mapping_path = tmp_path / "nested" / "caller-selected.json"
    input_path.write_text(
        json.dumps(
            {
                "recorded_at": "2026-08-03T04:05:06Z",
                "input_document_version_id": "document-version-batch",
                "records": [
                    {"prompt": "first", "response": {"value": 1}},
                    {"prompt": "second", "output": {"value": 2}},
                ],
            }
        ),
        encoding="utf-8",
    )

    record_fixture(input_path, mapping_path)
    payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    first = hashlib.sha256(b"first").hexdigest()
    second = hashlib.sha256(b"second").hexdigest()

    assert payload["recording_version"] == 1
    assert payload["recorded_at"] == "2026-08-03T04:05:06Z"
    assert payload["mappings"][first]["response"] == {"value": 1}
    assert payload["mappings"][second]["response"] == {"value": 2}
    assert all(
        entry["input_document_version_id"] == "document-version-batch"
        and entry["recorded_at"] == "2026-08-03T04:05:06Z"
        for entry in payload["mappings"].values()
    )


def test_recorder_cli_and_source_are_offline_only(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    mapping_path = tmp_path / "map.json"
    input_path.write_text(
        json.dumps(
            {
                "recorded_at": "2026-08-03T00:00:00Z",
                "document_version_id": "document-version-cli",
                "prompt": "cli prompt",
                "response": {"offline": True},
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(RECORDER),
            "--input-fixture",
            str(input_path),
            "--mapping-file",
            str(mapping_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(mapping_path.read_text(encoding="utf-8"))["mappings"]

    tree = ast.parse(RECORDER.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )
    assert imported.isdisjoint({"http", "httpx", "requests", "socket", "urllib"})
    assert "FixtureLLMClient" not in RECORDER.read_text(encoding="utf-8")
