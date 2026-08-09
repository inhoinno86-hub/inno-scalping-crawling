from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from tests.test_phase3_briefing_build import SETTINGS as BRIEFING_BUILD_SETTINGS
from tests.test_phase3_briefing_build import (
    _candidate as phase3_candidate,
    _database as phase3_database,
)
from tests.test_phase3_delivery_service import SETTINGS as DELIVERY_SERVICE_SETTINGS
from tests.test_phase3_delivery_service import (
    SpyConnector as Phase3SpyConnector,
    _briefing as phase3_briefing,
    _close as close_phase3_session,
    _session as phase3_session,
)

from scalping_briefing.models import (
    Briefing,
    BriefingItem,
    Delivery,
)
from scalping_briefing.orchestration import cycle
from scalping_briefing.orchestration.cycle import run_cycle
from scalping_briefing.pipeline.schedule import schedule_trigger


SCHEDULED_FOR = datetime(2026, 8, 7, 8, tzinfo=UTC)
SETTINGS = {
    **BRIEFING_BUILD_SETTINGS,
    **DELIVERY_SERVICE_SETTINGS,
    "TIMEZONE": "UTC",
    "LLM_MODE": "fixture",
    "DELIVERY_MODE": "dry_run",
    "DELIVERY_CHANNEL": "telegram",
    "publication_policy": "manual_approval",
}


def _payload(*, briefing_id: str = "briefing-fixture", approved_count: int = 1) -> dict[str, object]:
    return {
        "briefing_id": briefing_id,
        "scheduled_for": SCHEDULED_FOR,
        "trigger_type": "scheduled",
        "window_start": datetime(2026, 7, 24, 8, tzinfo=UTC),
        "window_end": SCHEDULED_FOR,
        "publication_status": "approved",
        "approved_count": approved_count,
        "items": [],
    }


def _wire_ops(monkeypatch, calls: list[str], tmp_path: Path) -> None:
    def metrics(*_args, **_kwargs):
        calls.append("metrics")
        return [SimpleNamespace(metric_id="M1", verdict="insufficient_data")]

    def render(*_args, **_kwargs):
        calls.append("report")
        return "# report\n"

    def archive(*_args, **_kwargs):
        calls.append("archive")
        return tmp_path / "report.md"

    def alerts(*_args, **_kwargs):
        calls.append("alerting")
        return []

    monkeypatch.setattr(cycle, "compute_all_metrics", metrics)
    monkeypatch.setattr(cycle, "render_report", render)
    monkeypatch.setattr(cycle, "archive_report", archive)
    monkeypatch.setattr(cycle, "emit_metric_alerts", alerts)


def _wire_cycle(monkeypatch, calls: list[str], payload: object) -> None:
    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        cycle,
        "build_briefing",
        lambda *_args, **_kwargs: calls.append("build") or payload,
    )


def test_run_cycle_wires_build_gate_delivery_in_order_and_defaults_connector(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    _wire_cycle(monkeypatch, calls, _payload())
    _wire_ops(monkeypatch, calls, tmp_path)

    connector = object()
    constructors: list[object] = []
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **kwargs: constructors.append(kwargs) or connector,
    )

    def gate(briefing, **_kwargs):
        calls.append("gate")
        return briefing

    def deliver(_session, _briefing, *, connector, **_kwargs):
        calls.append("delivery")
        assert connector is connector_fixture
        return SimpleNamespace(status="success")

    connector_fixture = connector
    monkeypatch.setattr(cycle, "gate_briefing", gate)
    monkeypatch.setattr(cycle, "deliver_briefing", deliver)

    summary = run_cycle(
        phase3_session(),
        settings=SETTINGS,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    assert calls[:4] == ["build", "gate", "delivery", "metrics"]
    assert constructors == [{"settings": SETTINGS}]
    assert summary.briefing_generated is True
    assert summary.delivery_invoked is True
    assert summary.delivery_status == "success"
    assert summary.failures == []


def test_zero_approved_briefing_is_normal_and_delivery_stage_runs_once(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    _wire_cycle(monkeypatch, calls, _payload(approved_count=0))
    _wire_ops(monkeypatch, calls, tmp_path)
    monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_: calls.append("gate") or briefing)
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **_kwargs: object(),
    )

    deliveries: list[object] = []

    def deliver(*_args, **_kwargs):
        deliveries.append(True)
        calls.append("delivery")
        return None

    monkeypatch.setattr(cycle, "deliver_briefing", deliver)

    summary = run_cycle(
        phase3_session(),
        settings=SETTINGS,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    assert deliveries == [True]
    assert calls[:3] == ["build", "gate", "delivery"]
    assert summary.delivery_invoked is True
    assert summary.delivery_status is None
    assert summary.status == "success"


def test_briefing_failure_skips_gate_and_delivery_but_runs_operational_stages(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def fail_build(*_args, **_kwargs):
        calls.append("build")
        raise RuntimeError("briefing build failed")

    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cycle, "build_briefing", fail_build)
    monkeypatch.setattr(
        cycle,
        "gate_briefing",
        lambda *_args, **_kwargs: calls.append("gate"),
    )
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("connector created")),
    )
    monkeypatch.setattr(
        cycle,
        "deliver_briefing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delivery called")
        ),
    )
    _wire_ops(monkeypatch, calls, tmp_path)

    summary = run_cycle(
        phase3_session(),
        settings=SETTINGS,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    assert calls == ["build", "metrics", "report", "archive", "alerting"]
    assert summary.briefing_generated is False
    assert summary.delivery_invoked is False
    assert summary.delivery_status is None
    assert summary.stages["briefing"].failed == 1
    assert summary.stages["gate"].processed == 0
    assert summary.stages["delivery"].processed == 0
    assert all(summary.stages[name].succeeded == 1 for name in ("metrics", "report", "alerting"))


def test_cycle_failure_masks_settings_delivery_credentials_in_summary_and_alert(
    monkeypatch, tmp_path: Path
) -> None:
    bot_token = "bot-settings-token-123"
    chat_id = "chat-settings-id-456"
    settings = {
        **SETTINGS,
        "TELEGRAM_BOT_TOKEN": bot_token,
        "TELEGRAM_CHAT_ID": chat_id,
    }

    def fail_build(*_args, **_kwargs):
        raise RuntimeError(f"delivery credentials: {bot_token} {chat_id}")

    monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cycle, "build_briefing", fail_build)
    _wire_ops(monkeypatch, [], tmp_path)

    summary = run_cycle(
        phase3_session(),
        settings=settings,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    serialized = summary.to_json()
    assert bot_token not in serialized
    assert chat_id not in serialized
    assert "[REDACTED]" in serialized
    artifacts = list((tmp_path / "alerts").glob("*.json"))
    assert artifacts
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in artifacts)
    assert bot_token not in artifact_text
    assert chat_id not in artifact_text
    assert "[REDACTED]" in artifact_text


def test_gate_failure_blocks_connector_but_runs_later_operational_stages(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    _wire_cycle(monkeypatch, calls, _payload())
    _wire_ops(monkeypatch, calls, tmp_path)
    monkeypatch.setattr(
        cycle,
        "gate_briefing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("gate failed")),
    )
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("connector created")),
    )
    monkeypatch.setattr(
        cycle,
        "deliver_briefing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("delivery called")),
    )

    summary = run_cycle(
        phase3_session(),
        settings=SETTINGS,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    assert summary.delivery_invoked is False
    assert summary.stages["gate"].failed == 1
    assert summary.failures[0].stage == "gate"
    assert calls[:4] == ["build", "metrics", "report", "archive"]
    assert "alerting" in calls


def test_live_delivery_mode_assembles_live_connector_and_invokes_delivery(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    _wire_cycle(monkeypatch, calls, _payload())
    _wire_ops(monkeypatch, calls, tmp_path)
    monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_: briefing)
    monkeypatch.setattr(
        cycle,
        "TelegramDryRunConnector",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run connector created in live mode")
        ),
    )

    created: list[object] = []

    class RecordingLiveConnector:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

    monkeypatch.setattr(cycle, "TelegramLiveConnector", RecordingLiveConnector)

    delivered: list[object] = []

    def deliver(*args: object, **kwargs: object) -> Any:
        delivered.append((args, kwargs))
        return SimpleNamespace(status="success")

    monkeypatch.setattr(cycle, "deliver_briefing", deliver)

    live_settings = {**SETTINGS, "DELIVERY_MODE": "live"}
    summary = run_cycle(
        phase3_session(),
        settings=live_settings,
        scheduled_for=SCHEDULED_FOR,
        alerts_dir=tmp_path / "alerts",
        report_output_dir=tmp_path / "reports",
    )

    assert summary.delivery_invoked is True
    assert summary.delivery_status == "success"
    assert len(created) == 1
    assert len(delivered) == 1


def test_same_trigger_leaves_one_briefing_and_delivery_row(monkeypatch, tmp_path: Path) -> None:
    engine, session, version = phase3_database(tmp_path)
    session.info["test_engine"] = engine
    candidate, evidence = phase3_candidate(
        version,
        "repeat-candidate",
        "approved",
        SCHEDULED_FOR,
    )
    session.add_all([candidate, *evidence])
    session.commit()
    try:
        bid = schedule_trigger(SCHEDULED_FOR, trigger_type="scheduled")["briefing_id"]

        monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])

        def build(session, *, scheduled_for, trigger_type, **_kwargs):
            briefing = session.get(Briefing, bid)
            if briefing is None:
                briefing = Briefing(
                    briefing_id=bid,
                    scheduled_for=scheduled_for,
                    trigger_type=trigger_type,
                    window_start=datetime(2026, 7, 24, 8, tzinfo=UTC),
                    window_end=scheduled_for,
                    run_status="success",
                    publication_status="approved",
                    generated_at=scheduled_for,
                    timezone="UTC",
                    source_summary={},
                    candidate_count=1,
                    approved_count=1,
                )
                briefing.items.append(
                    BriefingItem(
                        briefing_item_id="repeat-item",
                        strategy_candidate=candidate,
                        strategy_id="repeat-strategy",
                        reason_included="approved source-backed candidate",
                        rank=1,
                        evidence=evidence[:1],
                    )
                )
                session.add(briefing)
            session.flush()
            return briefing

        monkeypatch.setattr(cycle, "build_briefing", build)
        monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_: briefing)
        monkeypatch.setattr(cycle, "TelegramDryRunConnector", lambda **_: object())

        def deliver(session, briefing, **_kwargs):
            delivery = session.scalar(
                select(Delivery).where(Delivery.briefing_id == briefing.briefing_id)
            )
            if delivery is None:
                delivery = Delivery.for_briefing(
                    briefing_id=briefing.briefing_id,
                    channel="telegram",
                    content_hash="fixture-content",
                    status="success",
                )
                session.add(delivery)
                session.flush()
            return delivery

        monkeypatch.setattr(cycle, "deliver_briefing", deliver)
        _wire_ops(monkeypatch, [], tmp_path)

        run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )
        run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
        )

        assert len(session.scalars(select(Briefing)).all()) == 1
        assert len(session.scalars(select(Delivery)).all()) == 1
    finally:
        close_phase3_session(session)


def test_same_trigger_real_delivery_service_is_idempotent(
    monkeypatch, tmp_path: Path
) -> None:
    session = phase3_session()
    connector = Phase3SpyConnector()
    bid = schedule_trigger(SCHEDULED_FOR, trigger_type="scheduled")["briefing_id"]
    try:
        def build(session, *, scheduled_for, trigger_type, **_kwargs):
            briefing = session.get(Briefing, bid)
            if briefing is None:
                briefing = phase3_briefing()
                briefing.briefing_id = bid
                briefing.scheduled_for = scheduled_for
                briefing.trigger_type = trigger_type
                briefing.window_start = datetime(2026, 7, 24, 8, tzinfo=UTC)
                briefing.window_end = scheduled_for
                briefing.generated_at = scheduled_for
                briefing.timezone = "UTC"
                session.add(briefing)
            session.flush()
            return briefing

        monkeypatch.setattr(cycle, "collect_documents", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(cycle, "build_briefing", build)
        monkeypatch.setattr(cycle, "gate_briefing", lambda briefing, **_: briefing)
        _wire_ops(monkeypatch, [], tmp_path)

        run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
            connector=connector,
        )
        run_cycle(
            session,
            settings=SETTINGS,
            scheduled_for=SCHEDULED_FOR,
            alerts_dir=tmp_path / "alerts",
            report_output_dir=tmp_path / "reports",
            connector=connector,
        )

        assert len(session.scalars(select(Briefing)).all()) == 1
        assert len(session.scalars(select(Delivery)).all()) == 1
        assert len(connector.sent) == 1
    finally:
        close_phase3_session(session)
