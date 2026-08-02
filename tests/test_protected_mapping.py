from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from scalping_briefing.delivery.guard import (
    DeliveryHistory,
    ResendApprovalRequired,
    make_idempotency_key,
    next_attempt_no,
)
from scalping_briefing import run_briefing


ROOT = Path(__file__).resolve().parents[1]
MAPPING = {
    "P1": (
        "tests/test_net_guards.py::test_allowlist_rejection_happens_before_http_client_call",
        "tests/test_net_retry_robots.py::test_robots_decision_records_fields_and_tie_prefers_allow",
    ),
    "P2": ("tests/test_sanitize_gate.py::test_publication_gate_never_accepts_original_full_text",),
    "P3": ("tests/test_schemas.py::test_evidence_requires_document_version_id",),
    "P4": (
        "tests/test_schemas.py::test_field_status_and_robots_decision_contracts",
        "tests/test_protected_mapping.py::test_field_status_preserves_unknown_values",
    ),
    "P5": (
        "tests/test_connectors_repo_html.py::test_html_sanitize_runs_before_normalized_storage",
        "tests/test_sanitize_gate.py::test_sanitize_removes_executable_markup_and_preserves_injection_as_text",
    ),
    "P6": (
        "tests/test_sanitize_gate.py::test_publication_gate_rejects_banned_investment_language",
    ),
    "P7": (
        "tests/test_phase1_dod.py::test_phase1_dod2_changed_fixture_creates_new_version_with_change_summary",
    ),
    "P8": (
        "tests/test_models_migrations.py::test_delivery_guard_rejects_success_resend_without_two_part_approval",
        "tests/test_protected_mapping.py::test_delivery_idempotency_requires_approval",
    ),
    "P9": (
        "tests/test_config.py::test_defaults_cover_appendix_a_and_are_safe",
        "tests/test_config.py::test_explicit_approval_is_call_scoped",
        "tests/test_protected_mapping.py::test_run_briefing_is_fixture_dry_run",
    ),
    "P10": (
        "tests/test_logging_setup.py::test_environment_secret_is_masked_even_inside_message",
        "tests/test_protected_mapping.py::test_no_literal_secrets_in_repository",
    ),
}


def _collected_nodes() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_protected_mapping_references_real_pytest_nodes() -> None:
    document = (ROOT / "docs/protected-requirements-tests.md").read_text(
        encoding="utf-8"
    )
    collected = _collected_nodes()
    assert set(MAPPING) == {f"P{number}" for number in range(1, 11)}
    for requirement, nodes in MAPPING.items():
        assert requirement in document
        for node in nodes:
            assert f"`{node}`" in document
            assert node in collected


def test_field_status_preserves_unknown_values() -> None:
    schema = json.loads(
        (ROOT / "schemas/strategy_candidate.schema.json").read_text(encoding="utf-8")
    )
    assert set(schema["$defs"]["field_status_value"]["enum"]) == {
        "explicit",
        "inferred",
        "unknown",
        "conflicting",
        "not_applicable",
    }
    candidate = {
        "candidate_id": "candidate-1",
        "canonical_name": "Fixture candidate",
        "summary": "Bounded fixture summary.",
        "core_hypothesis": "",
        "core_hypothesis_status": "unknown",
        "signal_inputs": [],
        "signal_inputs_status": "unknown",
        "entry_logic": "",
        "entry_logic_status": "unknown",
        "exit_logic": "",
        "exit_logic_status": "unknown",
        "required_data": [],
        "required_data_status": "unknown",
        "risk_notes": "",
        "risk_notes_status": "unknown",
        "field_status": {"entry_logic": "unknown"},
        "relevance_status": "unknown",
        "review_status": "needs_review",
        "source_confidence": None,
        "extraction_confidence": None,
    }
    Draft202012Validator(schema).validate(candidate)
    candidate["entry_logic"] = "unverified text"
    candidate["entry_logic_status"] = "unknown"
    Draft202012Validator(schema).validate(candidate)
    candidate["entry_logic"] = ""
    candidate["entry_logic_status"] = "explicit"
    with pytest.raises(Exception):
        Draft202012Validator(schema).validate(candidate)


def test_delivery_idempotency_requires_approval() -> None:
    history = DeliveryHistory(status="success", attempt_no=1)
    assert make_idempotency_key("briefing-1", "telegram", "sha256-content") == (
        "briefing-1:telegram:sha256-content"
    )
    with pytest.raises(ResendApprovalRequired):
        next_attempt_no(history)
    assert next_attempt_no(
        history,
        resend_reason="operator-reviewed",
        resend_approved_by="reviewer-1",
    ) == 2


def test_run_briefing_is_fixture_dry_run(monkeypatch, tmp_path: Path, capsys) -> None:
    def blocked_socket(*_args, **_kwargs):
        raise AssertionError("dry-run fixture collection attempted a socket call")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.sqlite3'}")

    assert run_briefing() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["active_fixture_sources"] == 5
    assert payload["persisted_versions"] >= 8
    assert payload["briefing_generated"] is False
    assert payload["delivery_invoked"] is False
    assert payload["llm_mode"] == "fixture"
    assert payload["delivery_mode"] == "dry_run"


def test_no_prohibited_trading_modules_exist() -> None:
    prohibited = {"order", "execution", "trade", "backtest", "portfolio", "optimization"}
    matches = [
        path.relative_to(ROOT)
        for path in (ROOT / "src").rglob("*.py")
        if path.stem.lower() in prohibited
    ]
    assert matches == []


def test_no_literal_secrets_in_repository() -> None:
    patterns = (
        re.compile(r"\b(?:sk|rk|ghp|github_pat|xoxb|xoxp|AIza)[-_][A-Za-z0-9]{16,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bbot\d{6,}:[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    )
    ignored_parts = {
        ".git",
        ".venv",
        ".loop-engine",
        ".pytest_cache",
        "__pycache__",
        "storage",
        "data",
        "alerts",
    }
    violations: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ignored_parts.intersection(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in patterns):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []
