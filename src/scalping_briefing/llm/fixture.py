"""Hash-addressed, file-only LLM fixture client."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


DEFAULT_FIXTURE_MAPPING = Path(__file__).with_name("fixtures") / "response-map.json"


class FixtureLLMError(RuntimeError):
    """Base error for malformed or incomplete fixture mappings."""


class FixtureMappingMissingError(FixtureLLMError):
    """Raised when a prompt hash has no recorded response."""


class LLMClient(Protocol):
    """Minimal provider-independent boundary used by pipeline callers."""

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        ...


def prompt_hash(prompt: str | bytes) -> str:
    """Return the stable SHA-256 key for an exact prompt payload."""

    payload = prompt if isinstance(prompt, bytes) else prompt.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


hash_prompt = prompt_hash


def _mapping_section(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("mappings", "responses", "prompt_hashes"):
        section = payload.get(key)
        if isinstance(section, Mapping):
            return section
    # A plain {sha256: response} JSON object is also a valid recording.
    return payload


class FixtureLLMClient:
    """Resolve exact prompt hashes from one local JSON recording file.

    The client intentionally has no network fallback.  A missing hash is a
    hard failure so a test cannot silently become an unrecorded live call.
    """

    def __init__(
        self,
        mapping_path: str | Path | None = None,
        *,
        fixture_path: str | Path | None = None,
        responses_path: str | Path | None = None,
    ) -> None:
        selected = [item for item in (mapping_path, fixture_path, responses_path) if item is not None]
        if len(selected) > 1:
            raise TypeError("use one of mapping_path, fixture_path, or responses_path")
        self.mapping_path = Path(selected[0] if selected else DEFAULT_FIXTURE_MAPPING)
        if not self.mapping_path.is_file():
            raise FixtureLLMError(f"fixture mapping file not found: {self.mapping_path}")
        self.calls = 0
        self.last_prompt_hash: str | None = None

    def _load(self) -> Mapping[str, Any]:
        try:
            payload = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise FixtureLLMError(f"invalid fixture mapping: {self.mapping_path}") from exc
        if not isinstance(payload, Mapping):
            raise FixtureLLMError("fixture mapping root must be a JSON object")
        return _mapping_section(payload)

    @staticmethod
    def _response(record: Any) -> Any:
        if isinstance(record, Mapping):
            for key in ("response", "output", "completion"):
                if key in record:
                    return record[key]
        return record

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        if not isinstance(prompt, str):
            raise TypeError("fixture prompt must be a string")
        digest = prompt_hash(prompt)
        self.last_prompt_hash = digest
        mapping = self._load()
        if digest not in mapping:
            raise FixtureMappingMissingError(
                f"no fixture response for prompt hash {digest} in {self.mapping_path}"
            )
        self.calls += 1
        # JSON-backed values are copied so callers cannot mutate future calls.
        return copy.deepcopy(self._response(mapping[digest]))

    def metadata(self, prompt: str) -> Mapping[str, Any] | None:
        """Return recording metadata without exposing a live-call mechanism."""

        digest = prompt_hash(prompt)
        record = self._load().get(digest)
        if not isinstance(record, Mapping):
            return None
        return {
            key: copy.deepcopy(value)
            for key, value in record.items()
            if key not in {"response", "output", "completion"}
        }

    # Common names used by small pipeline adapters; all remain file-backed.
    invoke = complete
    generate = complete
    run = complete


__all__ = [
    "DEFAULT_FIXTURE_MAPPING",
    "FixtureLLMClient",
    "FixtureLLMError",
    "FixtureMappingMissingError",
    "LLMClient",
    "hash_prompt",
    "prompt_hash",
]
