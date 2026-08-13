"""Live LLM client that shells out to an already-authenticated Codex CLI.

Speaks the ``LLMClient`` protocol from :mod:`scalping_briefing.llm.fixture`
(``complete``, ``metadata``) by invoking ``codex exec`` as a subprocess and
reading its final message back from disk. Auth is whatever OAuth/ChatGPT-
subscription session lives under ``codex_home`` -- no ``OPENAI_API_KEY``,
no separate metered per-call billing. ``codex_home`` should point at a
*lean* Codex home (an ``auth.json`` copy with no ``config.toml``/
``hooks.json``): a full interactive Codex home loads ``AGENTS.md`` and
registered hooks on every call, which measured 55-90% higher token usage
per call for no benefit to a one-shot extraction prompt.

``--output-schema`` (OpenAI's server-side structured-output enforcement)
is deliberately not used: ``schemas/strategy_candidate.schema.json`` uses
``allOf``, which that endpoint rejects ("'allOf' is not permitted"). The
schema is instead embedded in the prompt text itself (already done by
``llm/prompts.py::build_extraction_prompt`` for every ``LLMClient``), and
the parsed response gets exactly one same-prompt retry on schema failure --
mirroring :class:`~scalping_briefing.llm.local_ollama.LocalLLMClient`.

Subprocess, timeout, and JSON-parse failures propagate to the caller, which
already has a failure-isolation path (``orchestration/cycle.py``'s
``run_stage``/``alerts/``).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scalping_briefing.llm.schema_guard import (
    SchemaValidationError,
    validate_strategy_candidate,
)

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_CODEX_BIN = "codex"
DEFAULT_CODEX_HOME = str(Path.home() / ".codex-lean-scalping")
# codex exec itself is cloud-hosted (not CPU-bound like local inference), but
# still carries real per-call latency; keep a generous ceiling rather than
# guess a tight one.
DEFAULT_TIMEOUT = 600.0
MAX_ATTEMPTS = 2


class CodexCLIError(RuntimeError):
    """Raised when ``codex exec`` exits non-zero or produces no usable output."""


class CodexLLMClient:
    """Call an authenticated Codex CLI session and return its parsed JSON output."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        codex_home: str | None = DEFAULT_CODEX_HOME,
        codex_bin: str = DEFAULT_CODEX_BIN,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.codex_home = codex_home
        self.codex_bin = codex_bin
        self.timeout = timeout
        self._last_prompt: str | None = None
        self._last_usages: list[Mapping[str, Any]] | None = None

    def _env(self) -> Mapping[str, str] | None:
        if self.codex_home is None:
            return None
        env = dict(os.environ)
        env["CODEX_HOME"] = self.codex_home
        return env

    def _run(self, prompt: str) -> tuple[Any, Mapping[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "response.txt"
            command = [
                self.codex_bin,
                "exec",
                "--sandbox",
                "read-only",
                "--model",
                self.model,
                "-c",
                f"model_reasoning_effort={self.reasoning_effort}",
                "--skip-git-repo-check",
                "--json",
                "--output-last-message",
                str(output_path),
                prompt,
            ]
            process = subprocess.run(
                command,
                cwd=tmp,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if process.returncode != 0:
                raise CodexCLIError(
                    f"codex exec exited {process.returncode}: {process.stderr[-2000:]}"
                )
            if not output_path.exists():
                raise CodexCLIError(
                    f"codex exec produced no output file; stdout tail: {process.stdout[-2000:]}"
                )
            parsed = json.loads(output_path.read_text(encoding="utf-8"))
            usage = _last_turn_usage(process.stdout)
            return parsed, usage

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        parsed, usage = self._run(prompt)
        usages = [usage]
        try:
            validate_strategy_candidate(parsed)
        except SchemaValidationError:
            if len(usages) < MAX_ATTEMPTS:
                parsed, usage = self._run(prompt)
                usages.append(usage)
        self._last_prompt = prompt
        self._last_usages = usages
        return parsed

    def metadata(self, prompt: str) -> Mapping[str, Any] | None:
        if self._last_usages is None or prompt != self._last_prompt:
            return None
        usages = self._last_usages
        return {
            "usage": {
                "input_tokens": sum(u.get("input_tokens") or 0 for u in usages),
                "cached_input_tokens": sum(
                    u.get("cached_input_tokens") or 0 for u in usages
                ),
                "output_tokens": sum(u.get("output_tokens") or 0 for u in usages),
                "call_count": len(usages),
            },
            "estimated_cost_usd": 0.0,
            "model_name": f"codex:{self.model}:{self.reasoning_effort}",
        }


def _last_turn_usage(stdout: str) -> Mapping[str, Any]:
    """Return the ``usage`` object from the final ``turn.completed`` JSONL event."""

    usage: Mapping[str, Any] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), Mapping
        ):
            usage = event["usage"]
    return usage


__all__ = [
    "DEFAULT_CODEX_HOME",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_TIMEOUT",
    "CodexCLIError",
    "CodexLLMClient",
]
