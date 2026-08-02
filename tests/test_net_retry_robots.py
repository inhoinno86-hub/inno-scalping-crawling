from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest

from scalping_briefing.logging_setup import configure_logging
from scalping_briefing.net.rate_limit import SourceRateLimiter
from scalping_briefing.net.robots import RobotsEvaluator, access_is_allowed, evaluate_robots
from scalping_briefing.net.retry import (
    CollectionFailedError,
    RetryPolicy,
)


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


def test_source_rate_limiter_is_keyed_and_clock_driven() -> None:
    clock = Clock(datetime(2026, 8, 2, tzinfo=timezone.utc))
    policy = {"rate_limit": {"requests_per_minute": 60, "burst": 1}}
    limiter = SourceRateLimiter(clock=clock)

    assert limiter.acquire("source-a", policy).allowed is True
    blocked = limiter.acquire("source-a", policy)
    assert blocked.allowed is False
    assert blocked.wait_seconds == pytest.approx(1.0)
    assert limiter.acquire("source-b", policy).allowed is True

    clock.advance(1)
    assert limiter.acquire("source-a", policy).allowed is True


def test_source_rate_limiter_uses_each_configured_source_policy() -> None:
    clock = Clock(datetime(2026, 8, 2, tzinfo=timezone.utc))
    limiter = SourceRateLimiter(
        {
            "fast": {"rate_limit": {"requests_per_minute": 60}},
            "slow": {"rate_limit": {"requests_per_minute": 1}},
        },
        clock=clock,
    )

    assert limiter.acquire("fast").allowed is True
    assert limiter.acquire("slow").allowed is True
    assert limiter.acquire("fast").wait_seconds == pytest.approx(1.0)
    assert limiter.acquire("slow").wait_seconds == pytest.approx(60.0)


def test_robots_decision_records_fields_and_tie_prefers_allow() -> None:
    evaluated_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
    text = """User-agent: scalping-briefing
Disallow: /private
Allow: /private/public
"""

    decision = evaluate_robots(
        text,
        "https://example.invalid/private/public/item",
        user_agent="scalping-briefing/0.1",
        evaluated_at=evaluated_at,
    )
    assert decision.robots_allowed is True
    assert decision.robots_rule_matched == "/private/public"
    assert decision.robots_evaluated_at == evaluated_at
    assert decision.access_decision_reason
    assert access_is_allowed(decision)

    denied = RobotsEvaluator(user_agent="*").evaluate(
        "https://example.invalid/private/item",
        "User-agent: *\nDisallow: /private\n",
        evaluated_at=evaluated_at,
    )
    assert denied.robots_allowed is False
    assert denied.robots_rule_matched == "/private"
    assert not access_is_allowed(denied)


def test_robots_groups_are_not_merged_across_blank_lines() -> None:
    decision = evaluate_robots(
        "User-agent: named\n\nUser-agent: *\nDisallow: /\n",
        "https://example.invalid/item",
        user_agent="named/1.0",
    )

    assert decision.robots_allowed is True
    assert decision.robots_rule_matched is None


def test_missing_robots_is_unknown_and_fails_closed() -> None:
    decision = evaluate_robots(
        None,
        "https://example.invalid/item",
        evaluated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    assert decision.robots_allowed == "unknown"
    assert not decision.allowed
    assert decision.robots_rule_matched is None


def test_retry_state_caps_and_writes_both_evidence_paths(tmp_path: Path) -> None:
    now = Clock(datetime(2026, 8, 2, tzinfo=timezone.utc))
    stream = StringIO()
    logger = logging.getLogger("test.retry.evidence")
    configure_logging(stream=stream, logger=logger)
    policy = RetryPolicy(
        max_collect_retries=3,
        clock=now,
        logger=logger,
        alerts_dir=tmp_path / "alerts",
    )

    first = policy.record_failure(TimeoutError("slow"), source_id="source-a")
    assert first.retry_count == 1
    assert first.terminal_error is False
    assert first.next_retry_at == now.value + timedelta(seconds=1)

    now.advance(1)
    second = policy.record_failure(TimeoutError("slow"), state=first)
    assert second.retry_count == 2
    assert second.next_retry_at == now.value + timedelta(seconds=2)

    now.advance(2)
    terminal = policy.record_failure(TimeoutError("slow"), state=second)
    assert terminal.retry_count == 3
    assert terminal.status == "failed"
    assert terminal.terminal_error is True
    assert terminal.next_retry_at is None
    assert terminal.alert_path is not None

    log_payload = json.loads(stream.getvalue().splitlines()[-1])
    assert log_payload["event"] == "collection_failure"
    assert log_payload["terminal_error"] is True
    alert_payload = json.loads(terminal.alert_path.read_text(encoding="utf-8"))
    assert alert_payload["event"] == "collection_failure"
    assert alert_payload["details"]["retry_count"] == 3
    assert alert_payload["details"]["terminal_error"] is True


def test_exponential_backoff_is_capped() -> None:
    policy = RetryPolicy(max_collect_retries=3)
    assert [policy.backoff_seconds(i) for i in (1, 2, 3, 7)] == [1, 2, 4, 60]


def test_run_with_retries_raises_only_at_terminal_cap(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []
    policy = RetryPolicy(max_collect_retries=3, alerts_dir=tmp_path / "alerts")

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("fixture collection failed")

    with pytest.raises(CollectionFailedError) as raised:
        policy.run(operation, sleeper=sleeps.append, source_id="fixture")

    assert calls == 3
    assert sleeps == [1.0, 2.0]
    assert raised.value.state.terminal_error is True
    assert len(list((tmp_path / "alerts").glob("*.json"))) == 1


def test_terminal_failure_always_emits_alert(tmp_path: Path) -> None:
    policy = RetryPolicy(max_collect_retries=1, alerts_dir=tmp_path / "alerts")

    state = policy.record_failure(
        RuntimeError("terminal fixture failure"),
        source_id="fixture",
        emit_alert=False,
    )

    assert state.terminal_error is True
    assert state.alert_path is not None
    assert state.alert_path.is_file()
