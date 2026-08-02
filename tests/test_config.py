from __future__ import annotations

from pathlib import Path

import pytest

from scalping_briefing.config import CONFIG_KEYS, ConfigError, load_config


ROOT = Path(__file__).resolve().parents[1]


def test_defaults_cover_appendix_a_and_are_safe() -> None:
    settings = load_config(environ={})

    assert set(settings) == set(CONFIG_KEYS)
    assert settings.LLM_MODE == "fixture"
    assert settings.DELIVERY_MODE == "dry_run"
    assert settings.DATABASE_URL == "sqlite:///./data/app.sqlite3"
    assert settings.REVIEW_API_BIND == "127.0.0.1"
    assert settings.WEEKLY_REPORT_SCHEDULE == ["TUE 08:00", "FRI 08:00"]
    assert settings.REVIEW_API_TOKEN is None
    assert settings.LLM_MONTHLY_BUDGET_USD is None
    assert settings.LLM_RUN_MAX_TOKENS is None


def test_undefined_key_access_fails() -> None:
    settings = load_config(environ={})

    with pytest.raises(KeyError):
        _ = settings["UNDEFINED_KEY"]
    with pytest.raises(AttributeError):
        _ = settings.UNDEFINED_KEY


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"LLM_MODE": "live"}, "LLM_MONTHLY_BUDGET_USD"),
        (
            {
                "LLM_MODE": "live",
                "LLM_MONTHLY_BUDGET_USD": "10",
                "LLM_RUN_MAX_TOKENS": "1000",
            },
            "explicit approval",
        ),
        ({"DELIVERY_MODE": "live"}, "explicit approval"),
        ({"REVIEW_API_BIND": "0.0.0.0"}, "127.0.0.1"),
    ],
)
def test_live_and_external_gates_fail_closed(
    overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_config(environ=overrides)


def test_explicit_approval_is_call_scoped() -> None:
    settings = load_config(
        environ={
            "LLM_MODE": "live",
            "LLM_MONTHLY_BUDGET_USD": "10",
            "LLM_RUN_MAX_TOKENS": "1000",
        },
        approvals={"llm_live"},
    )

    assert settings.LLM_MODE == "live"


def test_default_toml_and_env_example_exist() -> None:
    assert (ROOT / "config/default.toml").is_file()
    env_example = ROOT / ".env.example"
    assert env_example.is_file()
    env_keys = {
        line.split("=", 1)[0]
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    assert {key.upper() for key in CONFIG_KEYS} <= env_keys
