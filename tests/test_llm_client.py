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


def test_stable_content_key_survives_a_new_document_version_id(tmp_path) -> None:
    from scalping_briefing.llm.fixture import stable_prompt_key
    from scalping_briefing.llm.prompts import build_extraction_prompt

    recorded = build_extraction_prompt(
        {
            "document_version_id": "dv-recorded",
            "content_hash": "sha256:abc",
            "normalized_text": "Queue imbalance precedes entry.",
        },
        document_text="Queue imbalance precedes entry.",
    )
    replayed = build_extraction_prompt(
        {
            "document_version_id": "dv-fresh-uuid",
            "content_hash": "sha256:abc",
            "normalized_text": "Queue imbalance precedes entry.",
        },
        document_text="Queue imbalance precedes entry.",
    )
    assert prompt_hash(recorded) != prompt_hash(replayed)
    key = stable_prompt_key(recorded)
    assert key == stable_prompt_key(replayed)
    assert key == "stable:phase2-extraction-v1:sha256:abc"

    path = tmp_path / "responses.json"
    path.write_text(
        json.dumps({"mappings": {key: {"response": {"ok": True}}}}), encoding="utf-8"
    )
    client = FixtureLLMClient(path)
    assert client.complete(replayed) == {"ok": True}
    assert client.calls == 1
    assert client.last_stable_key == key


def test_stable_key_is_none_for_prompts_without_a_content_hash(tmp_path) -> None:
    from scalping_briefing.llm.fixture import stable_prompt_key

    assert stable_prompt_key("not a project prompt") is None

    path = tmp_path / "responses.json"
    path.write_text(json.dumps({"mappings": {}}), encoding="utf-8")
    client = FixtureLLMClient(path)
    with pytest.raises(FixtureMappingMissingError, match="prompt hash"):
        client.complete("not a project prompt")
    assert client.calls == 0


def test_stable_key_replay_substitutes_the_runtime_document_version_id(tmp_path) -> None:
    from scalping_briefing.llm.fixture import stable_prompt_key
    from scalping_briefing.llm.prompts import build_extraction_prompt

    prompt = build_extraction_prompt(
        {
            "document_version_id": "dv-fresh-uuid",
            "content_hash": "sha256:abc",
            "normalized_text": "Queue imbalance precedes entry.",
        },
        document_text="Queue imbalance precedes entry.",
    )
    key = stable_prompt_key(prompt)
    path = tmp_path / "responses.json"
    path.write_text(
        json.dumps(
            {
                "mappings": {
                    key: {
                        "response": {
                            "candidate_id": "c-1",
                            "document_version_ids": ["{{document_version_id}}"],
                            "metadata": {"nested": ["{{document_version_id}}"]},
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = FixtureLLMClient(path)

    response = client.complete(prompt)

    assert response["document_version_ids"] == ["dv-fresh-uuid"]
    assert response["metadata"]["nested"] == ["dv-fresh-uuid"]


def test_exact_hash_recordings_are_returned_verbatim(tmp_path) -> None:
    prompt = "PROMPT_VERSION: v1\nINPUT_JSON:\n{}"
    path = tmp_path / "responses.json"
    path.write_text(
        json.dumps(
            {
                "mappings": {
                    prompt_hash(prompt): {
                        "response": {"kept": "{{document_version_id}}"}
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    client = FixtureLLMClient(path)

    assert client.complete(prompt) == {"kept": "{{document_version_id}}"}
