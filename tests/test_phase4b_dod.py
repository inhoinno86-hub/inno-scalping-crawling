from __future__ import annotations

import json
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

import scalping_briefing as briefing_package
from test_phase3_briefing_build import SETTINGS as PHASE3_SETTINGS
from test_phase3_briefing_build import _candidate, _database
from test_phase3_delivery_service import ATTEMPTED_AT
from test_phase3_delivery_service import SETTINGS as DELIVERY_SETTINGS
from test_phase3_delivery_service import SpyConnector, _close
from test_protected_mapping import test_run_briefing_is_fixture_dry_run as _legacy_run_briefing_contract

from scalping_briefing.models import Briefing, CollectionJob, Delivery, StrategyCandidate
from scalping_briefing.ops.metrics import ObservationWindow
from scalping_briefing.orchestration import cycle
from scalping_briefing.orchestration.cycle import run_cycle
from scalping_briefing import run_briefing_cycle


SCHEDULED_FOR = datetime(2026, 8, 7, 8, tzinfo=UTC)
WINDOW = ObservationWindow(
    start=SCHEDULED_FOR - timedelta(days=14),
    end=SCHEDULED_FOR,
    timezone="UTC",
)
RAW_TOKEN = "phase4b-raw-token"
RAW_CHAT_ID = "phase4b-raw-chat-id"
FULL_SOURCE_TEXT = "phase4b-full-source-document-text-" + ("x" * 500)


def _settings(alerts_dir: Path, **overrides: Any) -> SimpleNamespace:
    values = {**PHASE3_SETTINGS, **DELIVERY_SETTINGS}
    values.update(
        {
            "DATABASE_URL": "sqlite://",
            "TIMEZONE": "UTC",
            "LLM_MODE": "fixture",
            "DELIVERY_MODE": "dry_run",
            "alerts_dir": alerts_dir,
            "DELIVERY_CHANNEL": "telegram",
            "publication_policy": "manual_approval",
        }
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _wire_candidate_pipeline(
    monkeypatch: Any,
    events: list[tuple[str, str]],
    *,
    failing_id: str | None = None,
    failure: str | None = None,
) -> None:
    monkeypatch.setattr(cycle, "_existing_candidates", lambda _session: [])

    def classify(document_version: Any, **_kwargs: Any) -> Any:
        identifier = str(document_version.document_version_id)
        events.append(("classify", identifier))
        return SimpleNamespace(status="relevant")

    def extract(document_version: Any, **_kwargs: Any) -> Any:
        identifier = str(document_version.document_version_id)
        events.append(("extract", identifier))
        if identifier == failing_id:
            raise RuntimeError(failure or "fixture extraction failed")
        document_version.processing_status = "extracted"
        candidate = {
            "candidate_id": f"candidate-{identifier}",
            "review_status": "needs_review",
            "extraction_confidence": 0.8,
        }
        return SimpleNamespace(
            candidate=candidate,
            evidence=[
                {
                    "field_name": "summary",
                    "quote": "bounded fixture evidence",
                }
            ],
        )

    def validate(extraction: Any, **_kwargs: Any) -> Any:
        candidate_id = str(extraction.candidate["candidate_id"])
        events.append(("validate", candidate_id.removeprefix("candidate-")))
        return extraction

    def link(document_version: Any, _candidate_id: str, _entries: Any, **_kwargs: Any) -> bool:
        events.append(("evidence", str(document_version.document_version_id)))
        return True

    def score(candidate: Any, document_version: Any, *_args: Any, **_kwargs: Any) -> Any:
        events.append(("score", str(document_version.document_version_id)))
        return SimpleNamespace(value_score=80)

    def novelty(candidate: Any, *_args: Any, **_kwargs: Any) -> Any:
        events.append(("novelty", str(candidate["candidate_id"]).removeprefix("candidate-")))
        return SimpleNamespace(status="novel")

    def route(candidate: Any, document_version: Any, **_kwargs: Any) -> Any:
        events.append(("route", str(document_version.document_version_id)))
        return SimpleNamespace(candidate=candidate)

    monkeypatch.setattr(cycle, "classify_document", classify)
    monkeypatch.setattr(cycle, "extract_strategy_candidate", extract)
    monkeypatch.setattr(cycle, "validate_extracted_candidate", validate)
    monkeypatch.setattr(cycle, "link_evidence", link)
    monkeypatch.setattr(cycle, "score_candidate", score)
    monkeypatch.setattr(cycle, "classify_novelty", novelty)
    monkeypatch.setattr(cycle, "route_candidate", route)


def _mark_briefing_approved(monkeypatch: Any) -> None:
    original_build = cycle.build_briefing

    def build(*args: Any, **kwargs: Any) -> Any:
        briefing = original_build(*args, **kwargs)
        # The fixture explicitly models a previously approved publication.
        briefing.publication_status = "approved"
        return briefing

    monkeypatch.setattr(cycle, "build_briefing", build)


def _mark_fixture_source_allowed(version: Any) -> None:
    version.robots_allowed = True
    version.access_status = "allowed"
    document = version.document
    document.robots_allowed = True
    document.access_status = "allowed"
    document.source.robots_allowed = True


class _EngineDouble:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _SessionDouble:
    def __init__(self) -> None:
        self.closed = False
        self.committed = False
        self.info: dict[str, Any] = {}

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        self.committed = True


def test_phase4b_dod1_cycle_runs_collection_through_dry_run_delivery_in_one_execution(
    monkeypatch: Any, capsys: Any, tmp_path: Path
) -> None:
    settings = _settings(tmp_path / "alerts")
    engine = _EngineDouble()
    session = _SessionDouble()
    events: list[tuple[str, str]] = []
    version = SimpleNamespace(document_version_id="dod1-version", processing_status="collected")

    monkeypatch.setenv("DELIVERY_MODE", "dry_run")
    monkeypatch.setattr(briefing_package, "load_config", lambda: settings)
    monkeypatch.setattr(briefing_package, "create_engine", lambda _url: engine)
    monkeypatch.setattr(briefing_package, "Session", lambda _engine: session)
    monkeypatch.setattr(cycle, "next_occurrence", lambda *_args, **_kwargs: SCHEDULED_FOR)
    _wire_candidate_pipeline(monkeypatch, events)

    def collect(*_args: Any, **_kwargs: Any) -> list[Any]:
        events.append(("collect", "cycle"))
        return [version]

    def build(*_args: Any, **_kwargs: Any) -> Any:
        events.append(("briefing", "cycle"))
        return SimpleNamespace(
            briefing_id="briefing-dod1",
            window_start=WINDOW.start,
            window_end=WINDOW.end,
        )

    def gate(*_args: Any, **_kwargs: Any) -> Any:
        events.append(("gate", "cycle"))
        return _args[0]

    def delivery(*_args: Any, **_kwargs: Any) -> Any:
        events.append(("delivery", "cycle"))
        assert settings.DELIVERY_MODE == "dry_run"
        return SimpleNamespace(status="success")

    def metrics(*_args: Any, **_kwargs: Any) -> list[Any]:
        events.append(("metrics", "cycle"))
        return []

    def report(*_args: Any, **_kwargs: Any) -> Path:
        events.append(("report", "cycle"))
        return tmp_path / "report.md"

    def alerting(*_args: Any, **_kwargs: Any) -> list[Any]:
        events.append(("alerting", "cycle"))
        return []

    monkeypatch.setattr(cycle, "collect_documents", collect)
    monkeypatch.setattr(cycle, "build_briefing", build)
    monkeypatch.setattr(cycle, "gate_briefing", gate)
    monkeypatch.setattr(cycle, "TelegramDryRunConnector", lambda **_kwargs: object())
    monkeypatch.setattr(cycle, "deliver_briefing", delivery)
    monkeypatch.setattr(cycle, "compute_all_metrics", metrics)
    monkeypatch.setattr(cycle, "_render_and_archive_report", report)
    monkeypatch.setattr(cycle, "emit_metric_alerts", alerting)

    assert run_briefing_cycle() == 0
    assert session.closed is True
    assert session.committed is True
    assert engine.disposed is True

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert output == json.dumps(payload, sort_keys=True) + "\n"
    assert [stage for stage, _identifier in events] == list(cycle.STAGE_NAMES)
    assert payload["status"] == "success"
    assert payload["llm_mode"] == "fixture"
    assert payload["delivery_mode"] == "dry_run"
    assert payload["briefing_generated"] is True
    assert payload["delivery_invoked"] is True
    assert payload["delivery_status"] == "success"
    assert payload["metrics"] == {f"M{number}": "insufficient_data" for number in range(1, 7)}
    assert payload["failures"] == []
    assert all(
        payload["stages"][stage] == {"processed": 1, "succeeded": 1, "failed": 0}
        for stage in cycle.STAGE_NAMES
    )


def test_phase4b_dod2_stage_failure_is_isolated_and_cycle_continues_with_alert(
    monkeypatch: Any, tmp_path: Path
) -> None:
    alerts_dir = tmp_path / "alerts"
    settings = _settings(
        alerts_dir,
        TELEGRAM_BOT_TOKEN=RAW_TOKEN,
        TELEGRAM_CHAT_ID=RAW_CHAT_ID,
    )
    events: list[tuple[str, str]] = []
    versions = [
        SimpleNamespace(document_version_id="dod2-failed", processing_status="collected"),
        SimpleNamespace(document_version_id="dod2-ok", processing_status="collected"),
    ]
    session = _SessionDouble()
    delivery_calls: list[object] = []

    monkeypatch.setenv("DELIVERY_MODE", "dry_run")
    _wire_candidate_pipeline(
        monkeypatch,
        events,
        failing_id="dod2-failed",
        failure=(
            f"TELEGRAM_BOT_TOKEN={RAW_TOKEN} chat_id={RAW_CHAT_ID} "
            f"source={FULL_SOURCE_TEXT}"
        ),
    )
    monkeypatch.setattr(
        cycle,
        "collect_documents",
        lambda *_args, **_kwargs: (events.append(("collect", "cycle")) or versions),
    )
    monkeypatch.setattr(
        cycle,
        "build_briefing",
        lambda *_args, **_kwargs: (
            events.append(("briefing", "cycle"))
            or SimpleNamespace(
                briefing_id="briefing-dod2",
                window_start=WINDOW.start,
                window_end=WINDOW.end,
            )
        ),
    )

    def failing_gate(*_args: Any, **_kwargs: Any) -> Any:
        events.append(("gate", "cycle"))
        raise RuntimeError("gate blocked after isolated item failure")

    def forbidden_delivery(*_args: Any, **_kwargs: Any) -> Any:
        delivery_calls.append((_args, _kwargs))
        raise AssertionError("failure path invoked delivery")

    monkeypatch.setattr(cycle, "gate_briefing", failing_gate)
    monkeypatch.setattr(cycle, "deliver_briefing", forbidden_delivery)
    monkeypatch.setattr(cycle, "compute_all_metrics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cycle, "_render_and_archive_report", lambda *_args, **_kwargs: tmp_path / "report.md")
    monkeypatch.setattr(cycle, "emit_metric_alerts", lambda *_args, **_kwargs: [])

    def blocked_socket(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("failure path attempted network access")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    summary = run_cycle(
        session,
        settings=settings,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=alerts_dir,
        report_output_dir=tmp_path / "reports",
    )

    assert summary.status != "success"
    assert summary.exit_code != 0
    assert summary.stages["extract"].to_payload() == {
        "processed": 2,
        "succeeded": 1,
        "failed": 1,
    }
    assert summary.stages["validate"].processed == 1
    assert summary.stages["route"].processed == 1
    assert summary.stages["delivery"].processed == 0
    assert summary.delivery_invoked is False
    assert delivery_calls == []
    assert [identifier for stage, identifier in events if stage == "extract"] == [
        "dod2-failed",
        "dod2-ok",
    ]
    assert ("route", "dod2-ok") in events
    assert ("route", "dod2-failed") not in events
    assert {failure.stage for failure in summary.failures} >= {"extract", "gate"}

    alert_files = sorted(alerts_dir.glob("*.json"))
    assert alert_files
    assert {str(path) for path in alert_files} == set(summary.alerts_written)
    summary_text = summary.to_json()
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in alert_files)
    for secret in (RAW_TOKEN, RAW_CHAT_ID, FULL_SOURCE_TEXT):
        assert secret not in summary_text
        assert secret not in artifact_text
    assert "[REDACTED]" in summary_text
    assert "[REDACTED]" in artifact_text


def test_phase4b_dod3_repeated_cycle_for_same_trigger_does_not_duplicate_briefing_or_delivery(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    session.info["test_engine"] = engine
    settings = _settings(tmp_path / "alerts")
    connector = SpyConnector()
    candidate, evidence = _candidate(
        version,
        "phase4b-dod3-approved",
        "approved",
        ATTEMPTED_AT,
    )
    _mark_fixture_source_allowed(version)
    session.add_all([candidate, *evidence])
    session.commit()
    _mark_briefing_approved(monkeypatch)
    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_kwargs: briefing)

    try:
        first = run_cycle(
            session,
            settings=settings,
            scheduled_for=SCHEDULED_FOR,
            trigger_type="scheduled",
            connector=connector,
            observation_window=WINDOW,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )
        second = run_cycle(
            session,
            settings=settings,
            scheduled_for=SCHEDULED_FOR,
            trigger_type="scheduled",
            connector=connector,
            observation_window=WINDOW,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )

        briefings = session.scalars(select(Briefing)).all()
        deliveries = session.scalars(select(Delivery)).all()
        assert len(briefings) == 1
        assert len(deliveries) == 1
        assert first.briefing_id == second.briefing_id == briefings[0].briefing_id
        assert connector.rendered == [connector.rendered[0]]
        assert connector.sent == [connector.sent[0]]
        assert len(connector.rendered) == 1
        assert len(connector.sent) == 1
        assert first.delivery_status == "success"
        assert second.exit_code != 0
    finally:
        _close(session)


def test_phase4b_dod4_cycle_never_auto_approves_candidates_and_never_sends_live(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    session.info["test_engine"] = engine
    settings = _settings(tmp_path / "alerts")
    connector = SpyConnector()
    candidate, evidence = _candidate(
        version,
        "phase4b-dod4-pending",
        "needs_review",
        ATTEMPTED_AT,
    )
    _mark_fixture_source_allowed(version)
    session.add_all([candidate, *evidence])
    session.commit()
    monkeypatch.setenv("DELIVERY_MODE", "dry_run")

    def blocked_socket(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("fixture cycle attempted network access")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])

    try:
        summary = run_cycle(
            session,
            settings=settings,
            scheduled_for=SCHEDULED_FOR,
            trigger_type="scheduled",
            connector=connector,
            observation_window=WINDOW,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )

        persisted = session.scalars(select(StrategyCandidate)).all()
        assert settings.LLM_MODE == "fixture"
        assert settings.DELIVERY_MODE == "dry_run"
        assert [row.review_status for row in persisted] == ["needs_review"]
        assert all(row.review_status != "approved" for row in persisted)
        assert connector.rendered == []
        assert connector.sent == []
        assert summary.stages["delivery"].processed == 0
        assert summary.delivery_invoked is False
    finally:
        _close(session)


def test_phase4b_dod5_cycle_emits_metrics_report_and_alerts_after_delivery(
    monkeypatch: Any, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    engine, session, version = _database(tmp_path)
    session.info["test_engine"] = engine
    settings = _settings(tmp_path / "alerts")
    connector = SpyConnector()
    candidate, evidence = _candidate(
        version,
        "phase4b-dod5-approved",
        "approved",
        ATTEMPTED_AT,
    )
    _mark_fixture_source_allowed(version)
    session.add_all([candidate, *evidence])
    session.add(
        CollectionJob(
            collection_job_id="phase4b-dod5-failed-collection",
            source_id=version.document.source_id,
            status="failed",
            scheduled_for=SCHEDULED_FOR - timedelta(hours=1),
            completed_at=SCHEDULED_FOR - timedelta(hours=1),
            terminal_error=True,
            error="bounded fixture collection failure",
        )
    )
    session.commit()
    _mark_briefing_approved(monkeypatch)
    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_kwargs: briefing)

    events: list[str] = []
    captured_metrics: list[Any] = []
    original_delivery = cycle.deliver_briefing
    original_metrics = cycle.compute_all_metrics
    original_report = cycle._render_and_archive_report
    original_alerting = cycle.emit_metric_alerts

    def delivery(*args: Any, **kwargs: Any) -> Any:
        events.append("delivery")
        return original_delivery(*args, **kwargs)

    def metrics(*args: Any, **kwargs: Any) -> Any:
        events.append("metrics")
        results = original_metrics(*args, **kwargs)
        captured_metrics.extend(results)
        return results

    def report(*args: Any, **kwargs: Any) -> Any:
        events.append("report")
        return original_report(*args, **kwargs)

    def alerting(*args: Any, **kwargs: Any) -> Any:
        events.append("alerting")
        return original_alerting(*args, **kwargs)

    monkeypatch.setattr(cycle, "deliver_briefing", delivery)
    monkeypatch.setattr(cycle, "compute_all_metrics", metrics)
    monkeypatch.setattr(cycle, "_render_and_archive_report", report)
    monkeypatch.setattr(cycle, "emit_metric_alerts", alerting)

    try:
        summary = run_cycle(
            session,
            settings=settings,
            scheduled_for=SCHEDULED_FOR,
            trigger_type="scheduled",
            connector=connector,
            observation_window=WINDOW,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )

        assert summary.status == "success"
        assert connector.sent
        assert events == ["delivery", "metrics", "report", "alerting"]
        assert [metric.metric_id for metric in captured_metrics] == [
            "M1",
            "M2",
            "M3",
            "M4",
            "M5",
            "M6",
        ]
        verdicts = {metric.verdict for metric in captured_metrics}
        assert {"breached", "insufficient_data"} <= verdicts
        assert set(summary.metrics) == {f"M{number}" for number in range(1, 7)}
        assert all(
            metric.verdict == summary.metrics[metric.metric_id]
            for metric in captured_metrics
        )
        insufficient = [
            metric
            for metric in captured_metrics
            if metric.verdict == "insufficient_data"
        ]
        assert insufficient
        assert all(
            metric.value is None
            and metric.meets_target is False
            and metric.sample_size == 0
            for metric in insufficient
        )

        report_path = Path(summary.report_path or "")
        assert report_path.is_file()
        report_text = report_path.read_text(encoding="utf-8")
        assert all(f"| M{number} |" in report_text for number in range(1, 7))

        expected_alerts = {
            f"{WINDOW.window_id}:{metric.metric_id}.json"
            for metric in captured_metrics
            if metric.verdict in {"breached", "insufficient_data"}
        }
        actual_alerts = {Path(path).name for path in summary.alerts_written}
        assert actual_alerts == expected_alerts
        for path in (tmp_path / "alerts").glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["event"].split(":", 1)[0] in {
                "metric_breach",
                "metric_insufficient_data",
            }
            assert "telegram" not in json.dumps(payload, ensure_ascii=False).lower()
    finally:
        _close(session)


def test_phase4b_dod6_run_briefing_entrypoint_contract_is_unchanged(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    _legacy_run_briefing_contract(monkeypatch, tmp_path, capsys)
