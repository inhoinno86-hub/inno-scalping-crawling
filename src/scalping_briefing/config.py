"""Strict, offline-first configuration loading for the briefing system."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when configuration is invalid or violates a safety gate."""


CONFIG_KEYS = (
    "PROJECT_SLUG",
    "TIMEZONE",
    "WEEKLY_REPORT_SCHEDULE",
    "initial_lookback_days",
    "max_lookback_days",
    "candidate_score_threshold",
    "briefing_max_items",
    "extraction_confidence_min",
    "quote_max_chars",
    "briefing_language",
    "publication_policy",
    "DELIVERY_CHANNEL",
    "DELIVERY_MODE",
    "LLM_MODE",
    "LLM_MONTHLY_BUDGET_USD",
    "LLM_RUN_MAX_TOKENS",
    "DATABASE_URL",
    "REVIEW_API_BIND",
    "REVIEW_API_TOKEN",
    "max_collect_retries",
    "response_max_bytes",
    "request_timeout_seconds",
    "max_redirects",
    "raw_retention_days",
    "normalized_retention_days",
    "llm_run_retention_days",
    "alerts_dir",
)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "default.toml"
)

_DEFAULTS: dict[str, Any] = {
    "PROJECT_SLUG": "scalping-briefing",
    "TIMEZONE": "Asia/Seoul",
    "WEEKLY_REPORT_SCHEDULE": ["TUE 08:00", "FRI 08:00"],
    "initial_lookback_days": 14,
    "max_lookback_days": 30,
    "candidate_score_threshold": 60,
    "briefing_max_items": 7,
    "extraction_confidence_min": 0.7,
    "quote_max_chars": 300,
    "briefing_language": "ko",
    "publication_policy": "manual_approval",
    "DELIVERY_CHANNEL": "telegram",
    "DELIVERY_MODE": "dry_run",
    "LLM_MODE": "fixture",
    "LLM_MONTHLY_BUDGET_USD": None,
    "LLM_RUN_MAX_TOKENS": None,
    "DATABASE_URL": "sqlite:///./data/app.sqlite3",
    "REVIEW_API_BIND": "127.0.0.1",
    "REVIEW_API_TOKEN": None,
    "max_collect_retries": 3,
    "response_max_bytes": 10 * 1024 * 1024,
    "request_timeout_seconds": 20,
    "max_redirects": 3,
    "raw_retention_days": 365,
    "normalized_retention_days": "unlimited",
    "llm_run_retention_days": 365,
    "alerts_dir": "alerts/",
}

_INT_KEYS = {
    "initial_lookback_days",
    "max_lookback_days",
    "candidate_score_threshold",
    "briefing_max_items",
    "quote_max_chars",
    "max_collect_retries",
    "response_max_bytes",
    "request_timeout_seconds",
    "max_redirects",
    "raw_retention_days",
    "llm_run_retention_days",
}
_FLOAT_KEYS = {"extraction_confidence_min"}
_OPTIONAL_KEYS = {
    "LLM_MONTHLY_BUDGET_USD",
    "LLM_RUN_MAX_TOKENS",
    "REVIEW_API_TOKEN",
}


class Settings(Mapping[str, Any]):
    """Immutable-ish mapping that rejects every undefined key access."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        unknown = set(values) - set(CONFIG_KEYS)
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"undefined configuration key(s): {names}")
        self._values = {key: values[key] for key in CONFIG_KEYS}

    def __getitem__(self, key: str) -> Any:
        if key not in CONFIG_KEYS:
            raise KeyError(f"undefined configuration key: {key}")
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(CONFIG_KEYS)

    def __len__(self) -> int:
        return len(CONFIG_KEYS)

    def __getattr__(self, name: str) -> Any:
        if name in CONFIG_KEYS:
            return self._values[name]
        raise AttributeError(f"undefined configuration key: {name}")

    def get(self, key: str, default: Any = None) -> Any:
        if key not in CONFIG_KEYS:
            raise KeyError(f"undefined configuration key: {key}")
        return self._values[key]

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    to_dict = as_dict


Config = Settings


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _coerce(key: str, value: Any) -> Any:
    if key in _OPTIONAL_KEYS:
        if value is None:
            return None
        if isinstance(value, str):
            value = _strip_env_value(value)
            if not value:
                return None

    if key == "WEEKLY_REPORT_SCHEDULE":
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()]
        raise ConfigError("WEEKLY_REPORT_SCHEDULE must be a list or comma-separated string")

    if isinstance(value, str):
        value = _strip_env_value(value)

    if key in _INT_KEYS:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be an integer") from exc

    if key in _FLOAT_KEYS:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be a number") from exc

    if key in {"LLM_MONTHLY_BUDGET_USD", "LLM_RUN_MAX_TOKENS"}:
        try:
            return float(value) if key.endswith("USD") else int(value)
        except (TypeError, ValueError) as exc:
            expected = "number" if key.endswith("USD") else "integer"
            raise ConfigError(f"{key} must be a {expected} when set") from exc

    if key == "normalized_retention_days":
        if isinstance(value, str) and value.lower() in {"unlimited", "infinite", "none"}:
            return "unlimited"
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(
                "normalized_retention_days must be an integer or 'unlimited'"
            ) from exc

    return value


def _approval_set(
    approvals: Iterable[str] | Mapping[str, bool] | str | bool | None,
) -> set[str]:
    if approvals is None:
        return set()
    if approvals is True:
        return {"*"}
    if approvals is False:
        return set()
    if isinstance(approvals, Mapping):
        return {str(key).lower() for key, enabled in approvals.items() if enabled}
    if isinstance(approvals, str):
        return {part.strip().lower() for part in approvals.split(",") if part.strip()}
    return {str(item).lower() for item in approvals}


def _approved(approvals: set[str], *aliases: str) -> bool:
    if "*" in approvals:
        return True
    return any(alias.lower() in approvals for alias in aliases)


def _validate(values: Mapping[str, Any], approvals: set[str]) -> None:
    if values["LLM_MODE"] not in {"fixture", "live"}:
        raise ConfigError("LLM_MODE must be 'fixture' or 'live'")
    if values["DELIVERY_MODE"] not in {"dry_run", "live"}:
        raise ConfigError("DELIVERY_MODE must be 'dry_run' or 'live'")
    if values["DELIVERY_CHANNEL"] != "telegram":
        raise ConfigError("DELIVERY_CHANNEL must be 'telegram' in Phase 0 + 1")
    if values["publication_policy"] not in {"manual_approval", "auto_publish"}:
        raise ConfigError("publication_policy has unsupported value")
    if not values["WEEKLY_REPORT_SCHEDULE"]:
        raise ConfigError("WEEKLY_REPORT_SCHEDULE must not be empty")
    if not 0 <= values["extraction_confidence_min"] <= 1:
        raise ConfigError("extraction_confidence_min must be between 0 and 1")
    for key in _INT_KEYS:
        if values[key] < 0:
            raise ConfigError(f"{key} must not be negative")
    if values["normalized_retention_days"] != "unlimited" and values[
        "normalized_retention_days"
    ] < 0:
        raise ConfigError("normalized_retention_days must not be negative")
    if values["LLM_MONTHLY_BUDGET_USD"] is not None and values[
        "LLM_MONTHLY_BUDGET_USD"
    ] < 0:
        raise ConfigError("LLM_MONTHLY_BUDGET_USD must not be negative")
    if values["LLM_RUN_MAX_TOKENS"] is not None and values["LLM_RUN_MAX_TOKENS"] < 1:
        raise ConfigError("LLM_RUN_MAX_TOKENS must be positive when set")

    if values["LLM_MODE"] == "live":
        if values["LLM_MONTHLY_BUDGET_USD"] is None or values["LLM_RUN_MAX_TOKENS"] is None:
            raise ConfigError(
                "LLM_MODE=live requires LLM_MONTHLY_BUDGET_USD and "
                "LLM_RUN_MAX_TOKENS"
            )
        if not _approved(
            approvals,
            "llm_live",
            "live_llm",
            "llm_mode_live",
            "llm",
            "live",
        ):
            raise ConfigError("LLM_MODE=live requires explicit approval")

    if values["DELIVERY_MODE"] == "live" and not _approved(
        approvals,
        "delivery_live",
        "live_delivery",
        "delivery_mode_live",
        "delivery",
        "live",
    ):
        raise ConfigError("DELIVERY_MODE=live requires explicit approval")

    if values["REVIEW_API_BIND"] != "127.0.0.1" and not _approved(
        approvals,
        "review_api_external",
        "external_review_api",
        "review_api_bind",
        "review_api",
        "external",
    ):
        raise ConfigError(
            "REVIEW_API_BIND must be 127.0.0.1 without explicit external-bind approval"
        )


def load_config(
    config_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    path: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    approvals: Iterable[str] | Mapping[str, bool] | str | bool | None = None,
    approval: Iterable[str] | Mapping[str, bool] | str | bool | None = None,
    approved_actions: Iterable[str] | Mapping[str, bool] | str | bool | None = None,
    allow_live: bool = False,
    allow_external_review_api: bool = False,
    approved: bool = False,
) -> Settings:
    """Load TOML defaults plus env overrides, then enforce fail-closed gates.

    Approval is deliberately an explicit call argument, not a persisted config
    key. This prevents a normal environment override from silently enabling live
    cost or external-delivery behavior.
    """

    if config_path is not None and path is not None:
        raise TypeError("use config_path or path, not both")
    if environ is not None and env is not None:
        raise TypeError("use environ or env, not both")
    selected_path = Path(path or config_path or DEFAULT_CONFIG_PATH)
    environment = os.environ if environ is None and env is None else (environ or env or {})

    try:
        with selected_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {selected_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {selected_path}: {exc}") from exc

    unknown = set(raw) - set(CONFIG_KEYS)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigError(f"undefined configuration key(s) in TOML: {names}")

    values = dict(_DEFAULTS)
    for key, value in raw.items():
        values[key] = _coerce(key, value)

    for key in CONFIG_KEYS:
        env_names = (key, key.upper())
        present_name = next((name for name in env_names if name in environment), None)
        if present_name is not None:
            values[key] = _coerce(key, environment[present_name])

    combined_approvals = _approval_set(approvals)
    combined_approvals.update(_approval_set(approval))
    combined_approvals.update(_approval_set(approved_actions))
    if allow_live:
        combined_approvals.update({"llm_live", "delivery_live"})
    if allow_external_review_api:
        combined_approvals.add("review_api_external")
    if approved:
        combined_approvals.add("*")

    _validate(values, combined_approvals)
    return Settings(values)


def load_settings(*args: Any, **kwargs: Any) -> Settings:
    """Compatibility alias with an explicit settings-oriented name."""

    return load_config(*args, **kwargs)


__all__ = [
    "CONFIG_KEYS",
    "Config",
    "ConfigError",
    "DEFAULT_CONFIG_PATH",
    "Settings",
    "load_config",
    "load_settings",
]
