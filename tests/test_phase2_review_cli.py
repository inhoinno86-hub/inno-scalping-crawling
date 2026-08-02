from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

from scalping_briefing.review import cli


def _run_cli(monkeypatch, capsys, fake_service, argv: list[str]) -> dict[str, object]:
    monkeypatch.setattr(cli, "ReviewService", fake_service)
    assert cli.main(["--database-url", "sqlite:///:memory:", *argv]) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_list_uses_review_service(monkeypatch, capsys) -> None:
    calls: list[str | None] = []

    class FakeService:
        def __init__(self, session) -> None:
            self.session = session

        def list_candidates(self, status=None):
            calls.append(status)
            return [{"candidate_id": "candidate-1", "review_status": status}]

    result = _run_cli(
        monkeypatch,
        capsys,
        FakeService,
        ["list", "--status", "needs_review"],
    )

    assert calls == ["needs_review"]
    assert result == {
        "candidates": [
            {"candidate_id": "candidate-1", "review_status": "needs_review"}
        ]
    }


def test_cli_show_includes_evidence(monkeypatch, capsys) -> None:
    class FakeService:
        def __init__(self, session) -> None:
            self.session = session

        def get_candidate(self, candidate_id):
            assert candidate_id == "candidate-1"
            return {
                "candidate_id": candidate_id,
                "source_link": "https://example.invalid/source",
                "document_version_id": "version-1",
                "evidence": [
                    {
                        "evidence_id": "evidence-1",
                        "document_version_id": "version-1",
                        "quote": "Evidence quote.",
                        "captured_at": datetime(2026, 8, 3, tzinfo=UTC),
                    }
                ],
            }

    result = _run_cli(monkeypatch, capsys, FakeService, ["show", "candidate-1"])

    assert result["source_link"] == "https://example.invalid/source"
    assert result["document_version_id"] == "version-1"
    assert result["evidence"][0]["quote"] == "Evidence quote."
    assert result["evidence"][0]["captured_at"] == "2026-08-03T00:00:00+00:00"


def test_cli_decide_records_review(monkeypatch, capsys) -> None:
    calls: list[tuple[str, str, str, str | None]] = []

    class FakeService:
        def __init__(self, session) -> None:
            self.session = session

        def record_decision(self, candidate_id, reviewer_id, decision, comment=None):
            calls.append((candidate_id, reviewer_id, decision, comment))
            return {
                "review_id": "review-1",
                "strategy_candidate_id": candidate_id,
                "reviewer_id": reviewer_id,
                "decision": decision,
                "comment": comment,
            }

    result = _run_cli(
        monkeypatch,
        capsys,
        FakeService,
        [
            "decide",
            "candidate-1",
            "--reviewer-id",
            "reviewer-1",
            "--decision",
            "approved",
            "--comment",
            "Evidence checked.",
        ],
    )

    assert calls == [
        ("candidate-1", "reviewer-1", "approved", "Evidence checked.")
    ]
    assert result["review"]["reviewer_id"] == "reviewer-1"
    assert result["review"]["decision"] == "approved"


def test_cli_module_has_no_network_imports() -> None:
    module_path = Path(cli.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not {
        "http",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "websocket",
    } & imported_modules
