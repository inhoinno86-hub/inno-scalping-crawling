from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scalping_briefing.llm.codex_cli import CodexCLIError, CodexLLMClient


def _valid_strategy_candidate(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "canonical_name": "Queue imbalance scalp",
        "summary": "Enter on order-book imbalance.",
        "core_hypothesis": "Queue imbalance precedes short moves.",
        "core_hypothesis_status": "explicit",
        "signal_inputs": ["order book depth"],
        "signal_inputs_status": "explicit",
        "entry_logic": "Enter when imbalance exceeds threshold.",
        "entry_logic_status": "explicit",
        "exit_logic": "Exit on mean reversion.",
        "exit_logic_status": "explicit",
        "required_data": ["level 2 book"],
        "required_data_status": "explicit",
        "risk_notes": "Cap position size.",
        "risk_notes_status": "explicit",
        "field_status": {"summary": "explicit"},
        "relevance_status": "relevant",
        "review_status": "pending",
        "source_confidence": 0.8,
        "extraction_confidence": 0.8,
    }


def _invalid_strategy_candidate(candidate_id: str) -> dict:
    candidate = _valid_strategy_candidate(candidate_id)
    candidate["field_status"] = {}
    return candidate


def _output_path(command: list[str]) -> Path:
    return Path(command[command.index("--output-last-message") + 1])


def _turn_completed_line(*, input_tokens: int, cached_input_tokens: int, output_tokens: int) -> str:
    return json.dumps(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
            },
        }
    )


def _fake_completed_process(
    command: list[str],
    payload: dict,
    *,
    input_tokens: int = 100,
    cached_input_tokens: int = 40,
    output_tokens: int = 50,
) -> MagicMock:
    _output_path(command).write_text(json.dumps(payload), encoding="utf-8")
    result = MagicMock()
    result.returncode = 0
    result.stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "t-1"}),
            _turn_completed_line(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
            ),
        ]
    )
    result.stderr = ""
    return result


def test_codex_llm_client_parses_output_last_message_as_json(monkeypatch) -> None:
    calls = []

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        calls.append(command)
        return _fake_completed_process(command, _valid_strategy_candidate("c-1"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    result = client.complete("some prompt")

    assert result["candidate_id"] == "c-1"
    assert len(calls) == 1
    assert calls[0][:2] == ["codex", "exec"]
    assert "--output-schema" not in calls[0]


def test_codex_llm_client_sets_codex_home_env_when_configured(monkeypatch) -> None:
    captured_env = {}

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        captured_env.update(env or {})
        return _fake_completed_process(command, _valid_strategy_candidate("c-1"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home="/tmp/lean-codex-home")
    client.complete("some prompt")

    assert captured_env["CODEX_HOME"] == "/tmp/lean-codex-home"


def test_codex_llm_client_metadata_maps_turn_completed_usage(monkeypatch) -> None:
    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        return _fake_completed_process(
            command,
            _valid_strategy_candidate("c-1"),
            input_tokens=16666,
            cached_input_tokens=11008,
            output_tokens=326,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(model="gpt-5.6-terra", reasoning_effort="medium", codex_home=None)
    client.complete("some prompt")
    metadata = client.metadata("some prompt")

    assert metadata == {
        "usage": {
            "input_tokens": 16666,
            "cached_input_tokens": 11008,
            "output_tokens": 326,
            "call_count": 1,
        },
        "estimated_cost_usd": 0.0,
        "model_name": "codex:gpt-5.6-terra:medium",
    }


def test_codex_llm_client_propagates_nonzero_exit(monkeypatch) -> None:
    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "invalid_json_schema"
        return result

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    with pytest.raises(CodexCLIError, match="invalid_json_schema"):
        client.complete("some prompt")


def test_codex_llm_client_propagates_timeout(monkeypatch) -> None:
    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    with pytest.raises(subprocess.TimeoutExpired):
        client.complete("some prompt")


def test_codex_llm_client_does_not_retry_when_first_response_passes_schema(
    monkeypatch,
) -> None:
    calls = []

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        calls.append(command)
        return _fake_completed_process(command, _valid_strategy_candidate("c-1"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    result = client.complete("some prompt")

    assert len(calls) == 1
    assert result["candidate_id"] == "c-1"


def test_codex_llm_client_retries_once_after_schema_validation_failure(
    monkeypatch,
) -> None:
    payloads = [_invalid_strategy_candidate("c-1"), _valid_strategy_candidate("c-2")]
    calls = []

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        calls.append(command)
        return _fake_completed_process(command, payloads.pop(0))

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    result = client.complete("some prompt")

    assert len(calls) == 2
    assert result["candidate_id"] == "c-2"
    metadata = client.metadata("some prompt")
    assert metadata["usage"]["call_count"] == 2


def test_codex_llm_client_returns_last_response_when_retry_also_fails_schema(
    monkeypatch,
) -> None:
    payloads = [_invalid_strategy_candidate("c-1"), _invalid_strategy_candidate("c-2")]
    calls = []

    def fake_run(command, *, cwd, env, capture_output, text, timeout):
        calls.append(command)
        return _fake_completed_process(command, payloads.pop(0))

    monkeypatch.setattr(subprocess, "run", fake_run)

    client = CodexLLMClient(codex_home=None)
    result = client.complete("some prompt")

    assert len(calls) == 2
    assert result["candidate_id"] == "c-2"
