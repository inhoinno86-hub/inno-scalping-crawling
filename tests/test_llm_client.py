from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from scalping_briefing.llm import (
    FixtureLLMClient,
    FixtureMappingMissingError,
    prompt_hash,
)
from scalping_briefing.llm.local_ollama import LocalLLMClient


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
    assert key == "stable:phase2-extraction-v2:sha256:abc"

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


def test_local_llm_client_parses_the_response_field_as_json(monkeypatch) -> None:
    raw = {
        "response": json.dumps({"candidate_id": "c-1"}),
        "eval_count": 42,
        "prompt_eval_count": 7,
    }
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = raw

    def fake_post(url, json, timeout):  # noqa: A002 - matches httpx.post signature
        assert url == "http://127.0.0.1:11434/api/generate"
        assert json["format"] == "json"
        assert json["options"]["temperature"] == 0.1
        assert json["stream"] is False
        return fake_response

    monkeypatch.setattr("scalping_briefing.llm.local_ollama.httpx.post", fake_post)

    client = LocalLLMClient()
    result = client.complete("some prompt")

    assert result == {"candidate_id": "c-1"}


def test_local_llm_client_metadata_maps_ollama_usage_fields(monkeypatch) -> None:
    raw = {
        "response": json.dumps(_valid_strategy_candidate("c-1")),
        "eval_count": 42,
        "prompt_eval_count": 7,
    }
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = raw
    monkeypatch.setattr(
        "scalping_briefing.llm.local_ollama.httpx.post",
        lambda url, json, timeout: fake_response,
    )

    client = LocalLLMClient(model="qwen2.5:7b-instruct-q4_K_M")
    client.complete("some prompt")
    metadata = client.metadata("some prompt")

    assert metadata == {
        "usage": {"input_tokens": 7, "output_tokens": 42, "call_count": 1},
        "estimated_cost_usd": 0.0,
        "model_name": "local:qwen2.5:7b-instruct-q4_K_M",
    }


def test_local_llm_client_propagates_network_failure(monkeypatch) -> None:
    def fake_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("scalping_briefing.llm.local_ollama.httpx.post", fake_post)

    client = LocalLLMClient()
    with pytest.raises(httpx.ConnectError):
        client.complete("some prompt")


def test_local_llm_client_propagates_json_decode_failure(monkeypatch) -> None:
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {"response": "not json"}
    monkeypatch.setattr(
        "scalping_briefing.llm.local_ollama.httpx.post",
        lambda url, json, timeout: fake_response,
    )

    client = LocalLLMClient()
    with pytest.raises(json.JSONDecodeError):
        client.complete("some prompt")


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


def _fake_response(payload: dict, *, eval_count: int, prompt_eval_count: int) -> MagicMock:
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "response": json.dumps(payload),
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
    }
    return fake_response


def test_local_llm_client_does_not_retry_when_first_response_passes_schema(
    monkeypatch,
) -> None:
    first = _fake_response(
        _valid_strategy_candidate("c-1"), eval_count=42, prompt_eval_count=7
    )
    calls = []

    def fake_post(url, json, timeout):  # noqa: A002 - matches httpx.post signature
        calls.append(json)
        return first

    monkeypatch.setattr("scalping_briefing.llm.local_ollama.httpx.post", fake_post)

    client = LocalLLMClient()
    result = client.complete("some prompt")

    assert len(calls) == 1
    assert result["candidate_id"] == "c-1"


def test_local_llm_client_retries_once_after_schema_validation_failure(
    monkeypatch,
) -> None:
    first = _fake_response(
        _invalid_strategy_candidate("c-1"), eval_count=10, prompt_eval_count=5
    )
    second = _fake_response(
        _valid_strategy_candidate("c-1"), eval_count=20, prompt_eval_count=8
    )
    responses = [first, second]
    calls = []

    def fake_post(url, json, timeout):  # noqa: A002 - matches httpx.post signature
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr("scalping_briefing.llm.local_ollama.httpx.post", fake_post)

    client = LocalLLMClient()
    result = client.complete("some prompt")

    assert len(calls) == 2
    assert result["field_status"] == {"summary": "explicit"}


def test_local_llm_client_returns_last_response_when_retry_also_fails_schema(
    monkeypatch,
) -> None:
    first = _fake_response(
        _invalid_strategy_candidate("c-1"), eval_count=10, prompt_eval_count=5
    )
    second = _fake_response(
        _invalid_strategy_candidate("c-2"), eval_count=20, prompt_eval_count=8
    )
    responses = [first, second]
    calls = []

    def fake_post(url, json, timeout):  # noqa: A002 - matches httpx.post signature
        calls.append(json)
        return responses.pop(0)

    monkeypatch.setattr("scalping_briefing.llm.local_ollama.httpx.post", fake_post)

    client = LocalLLMClient()
    result = client.complete("some prompt")

    assert len(calls) == 2
    assert result["candidate_id"] == "c-2"
    assert result["field_status"] == {}


def test_local_llm_client_metadata_sums_usage_across_retry_calls(monkeypatch) -> None:
    first = _fake_response(
        _invalid_strategy_candidate("c-1"), eval_count=10, prompt_eval_count=5
    )
    second = _fake_response(
        _valid_strategy_candidate("c-1"), eval_count=20, prompt_eval_count=8
    )
    responses = [first, second]

    monkeypatch.setattr(
        "scalping_briefing.llm.local_ollama.httpx.post",
        lambda url, json, timeout: responses.pop(0),
    )

    client = LocalLLMClient()
    client.complete("some prompt")
    metadata = client.metadata("some prompt")

    assert metadata["usage"]["input_tokens"] == 13
    assert metadata["usage"]["output_tokens"] == 30
    assert metadata["usage"]["call_count"] == 2
    assert metadata["estimated_cost_usd"] == 0.0
    assert metadata["model_name"] == "local:qwen2.5:7b-instruct-q4_K_M"
