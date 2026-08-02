"""Offline LLM boundary for Phase 0.

Only the file-backed fixture client is provided here.  No provider SDK,
network transport, or live-call fallback belongs in this package.
"""

from .fixture import (
    DEFAULT_FIXTURE_MAPPING,
    FixtureLLMClient,
    FixtureLLMError,
    FixtureMappingMissingError,
    LLMClient,
    hash_prompt,
    prompt_hash,
)

__all__ = [
    "DEFAULT_FIXTURE_MAPPING",
    "FixtureLLMClient",
    "FixtureLLMError",
    "FixtureMappingMissingError",
    "LLMClient",
    "hash_prompt",
    "prompt_hash",
]
