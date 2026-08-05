from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

from test_phase3_briefing_build import SETTINGS as PHASE3_SETTINGS
from test_protected_mapping import MAPPING as PROTECTED_MAPPING
from test_protected_mapping import ROOT

import scalping_briefing as briefing_package
from scalping_briefing import run_briefing_cycle
from scalping_briefing.orchestration import cycle


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"function not found: {name}")


def _git_head_text(relative_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"HEAD:{relative_path}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _target_body(makefile: str, target: str) -> str:
    match = re.search(
        rf"(?m)^{re.escape(target)}:\n(?P<body>(?:\t.*\n)+)",
        makefile,
    )
    assert match is not None, f"missing Makefile target: {target}"
    return match.group("body")


def test_run_briefing_cycle_imports_calls_and_returns_summary_contract(
    monkeypatch, capsys
) -> None:
    settings = SimpleNamespace(DATABASE_URL="sqlite://", **PHASE3_SETTINGS)
    engine = SimpleNamespace(disposed=False)
    session = SimpleNamespace(closed=False)
    summary = SimpleNamespace(
        to_json=lambda: '{"phase":"4b","status":"success"}',
        exit_code=7,
    )
    calls: list[object] = []

    def create_engine(url: str):
        calls.append(("create_engine", url))
        return engine

    def make_session(selected_engine):
        calls.append(("Session", selected_engine))
        assert selected_engine is engine
        return session

    def run_cycle(selected_session, *, settings):
        calls.append(("run_cycle", selected_session, settings))
        assert selected_session is session
        return summary

    def dispose() -> None:
        engine.disposed = True

    def close() -> None:
        session.closed = True

    engine.dispose = dispose
    session.close = close
    monkeypatch.setattr(briefing_package, "load_config", lambda: settings)
    monkeypatch.setattr(briefing_package, "create_engine", create_engine)
    monkeypatch.setattr(briefing_package, "Session", make_session)
    monkeypatch.setattr(cycle, "run_cycle", run_cycle)

    assert run_briefing_cycle() == 7
    assert calls == [
        ("create_engine", "sqlite://"),
        ("Session", engine),
        ("run_cycle", session, settings),
    ]
    assert session.closed is True
    assert engine.disposed is True
    assert capsys.readouterr().out == '{"phase":"4b","status":"success"}\n'
    assert "run_briefing_cycle" in briefing_package.__all__


def test_run_briefing_source_and_docstring_are_byte_for_byte_unchanged() -> None:
    relative_path = "src/scalping_briefing/__init__.py"
    current = (ROOT / relative_path).read_text(encoding="utf-8")
    baseline = _git_head_text(relative_path)
    assert _function_source(current, "run_briefing") == _function_source(
        baseline, "run_briefing"
    )
    assert "tests/test_protected_mapping.py::test_run_briefing_is_fixture_dry_run" in PROTECTED_MAPPING["P9"]


def test_run_briefing_cycle_is_adjacent_thin_wrapper_with_local_import() -> None:
    source = (ROOT / "src/scalping_briefing/__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    function_names = [node.name for node in top_level_functions]
    assert function_names[function_names.index("run_briefing") + 1] == "run_briefing_cycle"
    cycle_node = next(node for node in top_level_functions if node.name == "run_briefing_cycle")
    local_imports = [
        node
        for node in ast.walk(cycle_node)
        if isinstance(node, ast.ImportFrom)
        and node.module == "orchestration.cycle"
        and any(alias.name == "run_cycle" for alias in node.names)
    ]
    assert len(local_imports) == 1


def test_makefile_adds_cycle_target_without_changing_existing_target_bodies() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    baseline = _git_head_text("Makefile")
    for target in ("test", "run-briefing", "review-api"):
        assert _target_body(makefile, target) == _target_body(baseline, target)
    assert _target_body(makefile, "test") == "\tPYTHONPATH=src $(PYTEST) -q\n"
    assert _target_body(makefile, "run-briefing") == (
        '\tPYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing; raise SystemExit(run_briefing())"\n'
    )
    assert _target_body(makefile, "review-api") == (
        '\tPYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_review_api; raise SystemExit(run_review_api())"\n'
    )
    assert _target_body(makefile, "run-briefing-cycle") == (
        '\tPYTHONPATH=src $(PYTHON) -c "from scalping_briefing import run_briefing_cycle; raise SystemExit(run_briefing_cycle())"\n'
    )
    assert ".PHONY: test run-briefing run-briefing-cycle review-api" in makefile


def test_orchestration_document_covers_stages_contracts_and_fixture_outcomes() -> None:
    document_path = ROOT / "docs/orchestration-cycle.md"
    assert document_path.is_file()
    document = document_path.read_text(encoding="utf-8")
    stages = {
        "collect": "collect_documents",
        "classify": "classify_document",
        "extract": "extract_strategy_candidate",
        "validate": "validate_extracted_candidate",
        "evidence": "link_evidence",
        "score": "score_candidate",
        "novelty": "classify_novelty",
        "route": "route_candidate",
        "briefing": "build_briefing",
        "gate": "gate_briefing",
        "delivery": "deliver_briefing",
        "metrics": "compute_all_metrics",
        "report": "render_report",
        "alerting": "emit_metric_alerts",
    }
    for stage, function in stages.items():
        assert stage in document
        assert function in document

    for required in (
        "PLAN_v2 §3.5",
        "validated_payload",
        "scheduled_for",
        "trigger_type",
        "alerts.record_failure",
        "exit code",
        "CycleSummary.to_json",
        "run-briefing",
        "run-briefing-cycle",
        "zero-approved",
        "insufficient_data",
    ):
        assert required in document

    fixture_section = document.split(
        "## 기본 fixture 실행에서 무엇이 일어나는가", 1
    )[1]
    for required in (
        "briefing_build.py:541,568",
        "pending_approval",
        "briefing_gate.py:562-578",
        "approved",
        "published",
        "internal draft",
        "approved records",
        "auto-approves",
        "P15",
        "processed 1 / succeeded 1",
        "processed 7 / succeeded 7",
        "processed 6 / **failed 6**",
        "response-map.json",
        "partial_success",
        "exit code `1`",
        "M5: meets_target",
        "M1",
        "M2",
        "M3",
        "M4",
        "M6",
        "insufficient_data",
        "P4",
    ):
        assert required in fixture_section


def test_operations_document_changes_only_phase4b_procedure_and_entry() -> None:
    document = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    baseline = _git_head_text("docs/operations.md")
    marker = "## Deferred phases\n"
    assert document.split(marker, 1)[0] == baseline.split(marker, 1)[0]
    assert "Phase 4b: end-to-end orchestration wiring is implemented" in document
    assert "### Phase 4b cycle procedure" in document
    assert "make run-briefing-cycle" in document
    assert "run_briefing()" in document
    assert "run_briefing_cycle()" in document
    assert "No deferred phase, including Phase 4b, is activated by `make run-briefing`." in document
