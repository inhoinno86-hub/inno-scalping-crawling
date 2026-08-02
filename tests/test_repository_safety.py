from __future__ import annotations

import subprocess
from pathlib import Path

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_gitignore_blocks_secrets_and_runtime_artifacts() -> None:
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".env",
        "storage/",
        "alerts/",
        "data/",
        "*.sqlite3",
        "__pycache__/",
        ".loop-engine/",
        ".venv/",
        ".pytest_cache/",
    ):
        assert pattern in patterns


def test_only_runtime_markers_are_unignored() -> None:
    for directory in ("storage", "alerts", "data"):
        marker = ROOT / directory / ".gitkeep"
        assert marker.is_file()
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(marker)],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode != 0


def test_python_and_pytest_contract() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.11"
    pytest_config = pyproject["tool"]["pytest"]["ini_options"]
    assert "not integration" in pytest_config["addopts"]
    assert any("integration" in marker for marker in pytest_config["markers"])


def test_makefile_entrypoints_are_phase_limited() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "test:" in makefile
    assert "run-briefing:" in makefile
    assert "review-api:" in makefile
    assert "dry_run" not in makefile or "run-briefing" in makefile
