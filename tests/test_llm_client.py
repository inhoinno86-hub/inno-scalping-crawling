from __future__ import annotations

import json

import pytest

from scalping_briefing.llm import (
    FixtureLLMClient,
    FixtureMappingMissingError,
    prompt_hash,
)


def test_fixture_client_uses_file_hash_mapping_only(tmp_path) -> None:
    prompt = "offline prompt"
    path = tmp_path / "responses.json"
    path.write_text(
        json.dumps({"mappings": {prompt_hash(prompt): {"response": {"ok": True}}}}),
        encoding="utf-8",
    )
    client = FixtureLLMClient(path)
    assert client.complete(prompt) == {"ok": True}
    assert client.calls == 1
    assert client.last_prompt_hash == prompt_hash(prompt)


def test_missing_fixture_hash_fails_immediately_without_fallback(tmp_path) -> None:
    path = tmp_path / "responses.json"
    path.write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    client = FixtureLLMClient(mapping_path=path)
    with pytest.raises(FixtureMappingMissingError, match="prompt hash"):
        client.complete("not recorded")
    assert client.calls == 0
