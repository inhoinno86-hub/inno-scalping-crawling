"""Phase 2 §3.4 wiring check: run_briefing_cycle()/run_cycle() can pass a
real ``llm_client`` through to the extraction stage, opt-in only.

No network calls are made here -- ``LocalLLMClient`` is monkeypatched to a
recording stub, so this file carries no ``integration`` marker and runs as
part of the default ``make test`` / ``pytest`` invocation.
"""

from __future__ import annotations

from types import SimpleNamespace

from scalping_briefing.orchestration import cycle
from scalping_briefing.orchestration.cycle import CycleSummary, run_candidate_stages

import scalping_briefing as briefing_package
from scalping_briefing import run_briefing_cycle


SETTINGS = {
    "candidate_score_threshold": 60,
    "extraction_confidence_min": 0.7,
    "quote_max_chars": 300,
}


def _version() -> dict[str, object]:
    return {
        "document_version_id": "dv-1",
        "processing_status": "deduplicated",
        "normalized_text": "Queue imbalance precedes entry.",
    }


def _wire_minimal_fakes(monkeypatch, *, extract_calls: list[dict[str, object]]) -> None:
    def classify(document_version, **kwargs):
        document_version["processing_status"] = "extracted"
        return SimpleNamespace(
            status="relevant",
            reason={"decision": "relevant"},
            as_dict=lambda: {"status": "relevant", "reason": {"decision": "relevant"}},
        )

    def extract(document_version, **kwargs):
        extract_calls.append(kwargs)
        return SimpleNamespace(
            candidate=None,
            evidence=[],
            error_class="stop_here",
            validated_payload=None,
        )

    monkeypatch.setattr(cycle, "classify_document", classify)
    monkeypatch.setattr(cycle, "extract_strategy_candidate", extract)


def test_run_candidate_stages_without_llm_client_omits_it_from_extraction(
    monkeypatch, tmp_path
) -> None:
    """Default behavior: no llm_client argument, no llm_client kwarg reaches
    extract_strategy_candidate, so its own FixtureLLMClient() default applies."""

    extract_calls: list[dict[str, object]] = []
    _wire_minimal_fakes(monkeypatch, extract_calls=extract_calls)
    summary = CycleSummary()

    run_candidate_stages(
        None,
        [_version()],
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert len(extract_calls) == 1
    assert "llm_client" not in extract_calls[0]


def test_run_candidate_stages_explicit_llm_client_reaches_extraction(
    monkeypatch, tmp_path
) -> None:
    """An explicit llm_client argument is forwarded to extract_strategy_candidate."""

    extract_calls: list[dict[str, object]] = []
    _wire_minimal_fakes(monkeypatch, extract_calls=extract_calls)
    summary = CycleSummary()
    sentinel_client = object()

    run_candidate_stages(
        None,
        [_version()],
        settings=SETTINGS,
        summary=summary,
        alerts_dir=tmp_path,
        llm_client=sentinel_client,
    )

    assert len(extract_calls) == 1
    assert extract_calls[0]["llm_client"] is sentinel_client


def test_run_candidate_stages_explicit_llm_client_overrides_settings_supplied_one(
    monkeypatch, tmp_path
) -> None:
    """Backward compatibility: a settings object that already exposes an
    ``llm_client`` attribute (an existing test-double mechanism via
    ``_setting_kwargs``) still works when the new parameter is not given, and
    an explicit ``llm_client`` argument takes priority over it when both are
    supplied."""

    extract_calls: list[dict[str, object]] = []
    _wire_minimal_fakes(monkeypatch, extract_calls=extract_calls)
    settings_client = object()
    settings = SimpleNamespace(**SETTINGS, llm_client=settings_client)
    summary = CycleSummary()

    run_candidate_stages(
        None,
        [_version()],
        settings=settings,
        summary=summary,
        alerts_dir=tmp_path,
    )

    assert extract_calls[0]["llm_client"] is settings_client

    extract_calls.clear()
    override_client = object()
    run_candidate_stages(
        None,
        [_version()],
        settings=settings,
        summary=summary,
        alerts_dir=tmp_path,
        llm_client=override_client,
    )

    assert extract_calls[0]["llm_client"] is override_client


def test_run_briefing_cycle_without_llm_client_keeps_fixture_default_wiring(
    monkeypatch, capsys
) -> None:
    """No llm_client argument and LLM_MODE unset/"fixture": run_cycle is
    called exactly as before this parameter existed (no llm_client kwarg),
    so run_briefing()'s / run_briefing_cycle()'s existing contract holds."""

    settings = SimpleNamespace(DATABASE_URL="sqlite://", LLM_MODE="fixture")
    engine = SimpleNamespace(disposed=False, dispose=lambda: None)
    session = SimpleNamespace(closed=False, close=lambda: None)
    summary = SimpleNamespace(to_json=lambda: '{"phase":"4b"}', exit_code=0)
    calls: list[tuple[object, ...]] = []

    def run_cycle(selected_session, *, settings):
        calls.append((selected_session, settings))
        return summary

    monkeypatch.setattr(briefing_package, "load_config", lambda: settings)
    monkeypatch.setattr(briefing_package, "create_engine", lambda url: engine)
    monkeypatch.setattr(briefing_package, "Session", lambda selected_engine: session)
    monkeypatch.setattr(cycle, "run_cycle", run_cycle)

    assert run_briefing_cycle() == 0
    assert calls == [(session, settings)]
    capsys.readouterr()


def test_run_briefing_cycle_assembles_local_llm_client_when_live(monkeypatch) -> None:
    """LLM_MODE=="live" opts the factory into building a LocalLLMClient and
    forwarding it to run_cycle -- with no real Ollama network call, since
    LocalLLMClient itself is monkeypatched to a recording stub."""

    settings = SimpleNamespace(
        DATABASE_URL="sqlite://",
        LLM_MODE="live",
        LLM_MONTHLY_BUDGET_USD=0,
        LLM_RUN_MAX_TOKENS=2000,
    )
    engine = SimpleNamespace(disposed=False, dispose=lambda: None)
    session = SimpleNamespace(closed=False, close=lambda: None)
    summary = SimpleNamespace(to_json=lambda: '{"phase":"4b"}', exit_code=0)
    run_cycle_calls: list[dict[str, object]] = []
    constructed: list[object] = []

    class RecordingLocalLLMClient:
        def __init__(self, max_tokens: object = None) -> None:
            self.max_tokens = max_tokens
            constructed.append(self)

    def run_cycle(selected_session, *, settings, llm_client=None):
        run_cycle_calls.append(
            {"session": selected_session, "settings": settings, "llm_client": llm_client}
        )
        return summary

    monkeypatch.setattr(briefing_package, "load_config", lambda: settings)
    monkeypatch.setattr(briefing_package, "create_engine", lambda url: engine)
    monkeypatch.setattr(briefing_package, "Session", lambda selected_engine: session)
    monkeypatch.setattr(cycle, "run_cycle", run_cycle)
    monkeypatch.setattr(
        "scalping_briefing.llm.local_ollama.LocalLLMClient", RecordingLocalLLMClient
    )

    assert run_briefing_cycle() == 0
    assert len(constructed) == 1
    assert constructed[0].max_tokens == 2000
    assert run_cycle_calls == [
        {"session": session, "settings": settings, "llm_client": constructed[0]}
    ]


def test_run_briefing_cycle_explicit_llm_client_argument_bypasses_factory(
    monkeypatch,
) -> None:
    """An explicit llm_client argument to run_briefing_cycle() is used
    as-is, without consulting LLM_MODE or constructing a LocalLLMClient."""

    settings = SimpleNamespace(DATABASE_URL="sqlite://", LLM_MODE="fixture")
    engine = SimpleNamespace(disposed=False, dispose=lambda: None)
    session = SimpleNamespace(closed=False, close=lambda: None)
    summary = SimpleNamespace(to_json=lambda: '{"phase":"4b"}', exit_code=0)
    run_cycle_calls: list[dict[str, object]] = []
    caller_supplied_client = object()

    def run_cycle(selected_session, *, settings, llm_client=None):
        run_cycle_calls.append({"llm_client": llm_client})
        return summary

    monkeypatch.setattr(briefing_package, "load_config", lambda: settings)
    monkeypatch.setattr(briefing_package, "create_engine", lambda url: engine)
    monkeypatch.setattr(briefing_package, "Session", lambda selected_engine: session)
    monkeypatch.setattr(cycle, "run_cycle", run_cycle)

    assert run_briefing_cycle(llm_client=caller_supplied_client) == 0
    assert run_cycle_calls == [{"llm_client": caller_supplied_client}]
