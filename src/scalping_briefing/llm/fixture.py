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

STABLE_KEY_PREFIX = "stable"


def stable_prompt_key(prompt: str | bytes) -> str | None:
    """Return a recording key that survives a new document version id.

    A prompt built by :mod:`scalping_briefing.llm.prompts` embeds the row's
    ``document_version_id``, which is a fresh UUID for every ingestion, so a
    recording keyed only by prompt hash can never be replayed against a
    rebuilt database.  This key names the same call by what is stable about
    it instead: the prompt version and the document's content hash.

    Returns ``None`` when the prompt is not in that format, which keeps the
    exact-hash contract as the only lookup for anything else.
    """

    text = prompt.decode("utf-8", errors="replace") if isinstance(prompt, bytes) else prompt
    if not isinstance(text, str):
        return None
    version: str | None = None
    payload_start: int | None = None
    for line in text.splitlines():
        if line.startswith("PROMPT_VERSION: "):
            version = line[len("PROMPT_VERSION: ") :].strip()
        elif line == "INPUT_JSON:":
            payload_start = text.index("INPUT_JSON:") + len("INPUT_JSON:")
            break
    if not version or payload_start is None:
        return None
    try:
        payload = json.loads(text[payload_start:])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    content_hash = payload.get("content_hash")
    if not isinstance(content_hash, str) or not content_hash.strip():
        return None
    return f"{STABLE_KEY_PREFIX}:{version}:{content_hash.strip()}"


DOCUMENT_VERSION_PLACEHOLDER = "{{document_version_id}}"


def _prompt_payload(prompt: str) -> Mapping[str, Any] | None:
    marker = "INPUT_JSON:"
    if marker not in prompt:
        return None
    try:
        payload = json.loads(prompt[prompt.index(marker) + len(marker) :])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _substitute(value: Any, replacement: str) -> Any:
    """Replace the document-version placeholder anywhere in a response."""

    if isinstance(value, str):
        return value.replace(DOCUMENT_VERSION_PLACEHOLDER, replacement)
    if isinstance(value, Mapping):
        return {key: _substitute(item, replacement) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, replacement) for item in value]
    return value


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
        self.last_stable_key: str | None = None

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
        stable_key = stable_prompt_key(prompt)
        self.last_prompt_hash = digest
        self.last_stable_key = stable_key
        mapping = self._load()
        key = self._resolve(mapping, digest, stable_key)
        if key is None:
            stable_note = f" or stable key {stable_key}" if stable_key else ""
            raise FixtureMappingMissingError(
                f"no fixture response for prompt hash {digest}{stable_note} "
                f"in {self.mapping_path}"
            )
        self.calls += 1
        # JSON-backed values are copied so callers cannot mutate future calls.
        response = copy.deepcopy(self._response(mapping[key]))
        if key == digest:
            # An exact recording replays byte for byte.
            return response
        # A content-addressed recording predates the row it is replayed
        # against, so it names that row by placeholder.  Downstream contracts
        # require the real id (candidate document_version_ids, evidence).
        payload = _prompt_payload(prompt) or {}
        version_id = payload.get("document_version_id")
        if not isinstance(version_id, str) or not version_id:
            return response
        return _substitute(response, version_id)

    @staticmethod
    def _resolve(
        mapping: Mapping[str, Any], digest: str, stable_key: str | None
    ) -> str | None:
        """Prefer the exact recording, then the content-addressed one."""

        if digest in mapping:
            return digest
        if stable_key is not None and stable_key in mapping:
            return stable_key
        return None

    def metadata(self, prompt: str) -> Mapping[str, Any] | None:
        """Return recording metadata without exposing a live-call mechanism."""

        mapping = self._load()
        key = self._resolve(mapping, prompt_hash(prompt), stable_prompt_key(prompt))
        record = mapping.get(key) if key is not None else None
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
    "DOCUMENT_VERSION_PLACEHOLDER",
    "FixtureLLMClient",
    "FixtureLLMError",
    "FixtureMappingMissingError",
    "LLMClient",
    "STABLE_KEY_PREFIX",
    "hash_prompt",
    "prompt_hash",
    "stable_prompt_key",
]
