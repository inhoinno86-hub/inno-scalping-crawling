"""Live LLM client for a locally hosted Ollama model (Phase 2 extraction).

Speaks the ``LLMClient`` protocol from :mod:`scalping_briefing.llm.fixture`
(``complete``, ``metadata``) against Ollama's ``/api/generate`` REST endpoint.
Network or JSON-parse failures propagate to the caller, which already has a
failure-isolation path (``orchestration/cycle.py``'s ``run_stage``/
``alerts/``). Schema validation failures get exactly one same-prompt retry
(hard-coded cap of two HTTP calls); if the retry also fails schema
validation, the second (last) response is returned as-is without raising.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

from scalping_briefing.llm.schema_guard import (
    SchemaValidationError,
    validate_strategy_candidate,
)

DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 60.0
MAX_ATTEMPTS = 2


class LocalLLMClient:
    """Call a local Ollama model and return its parsed JSON completion."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._last_prompt: str | None = None
        self._last_raw_responses: list[Mapping[str, Any]] | None = None

    def _post(self, payload: dict[str, Any]) -> Mapping[str, Any]:
        url = f"{self.base_url}/api/generate"
        response = httpx.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        raw = self._post(payload)
        parsed = json.loads(raw["response"])
        raws = [raw]
        try:
            validate_strategy_candidate(parsed)
        except SchemaValidationError:
            if len(raws) < MAX_ATTEMPTS:
                raw = self._post(payload)
                parsed = json.loads(raw["response"])
                raws.append(raw)
        self._last_prompt = prompt
        self._last_raw_responses = raws
        return parsed

    def metadata(self, prompt: str) -> Mapping[str, Any] | None:
        if self._last_raw_responses is None or prompt != self._last_prompt:
            return None
        raws = self._last_raw_responses
        return {
            "usage": {
                "input_tokens": sum(raw.get("prompt_eval_count") or 0 for raw in raws),
                "output_tokens": sum(raw.get("eval_count") or 0 for raw in raws),
                "call_count": len(raws),
            },
            "estimated_cost_usd": 0.0,
            "model_name": f"local:{self.model}",
        }


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "LocalLLMClient",
]
