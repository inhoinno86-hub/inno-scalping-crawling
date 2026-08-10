"""Offline loader and guardrails for the versioned Source Policy."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE_POLICY = ROOT / "config" / "source-policy.yaml"
DEFAULT_SOURCE_SCHEMA = ROOT / "schemas" / "source.schema.json"
FIXTURE_SOURCE_IDS = frozenset(
    {
        "fixture_rss_blog",
        "fixture_atom_research",
        "fixture_github_repo",
        "fixture_exchange_docs",
        "fixture_paper_meta",
    }
)


class SourcePolicyError(ValueError):
    """Raised when source policy data violates Phase 0 rules."""


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency/environment guard
        raise SourcePolicyError("source policy loading requires PyYAML") from exc
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SourcePolicyError(f"invalid source policy: {path}") from exc
    if not isinstance(payload, dict):
        raise SourcePolicyError("source policy root must be a mapping")
    return payload


def validate_source_policy(
    policy: dict[str, Any],
    *,
    schema_path: str | Path = DEFAULT_SOURCE_SCHEMA,
) -> dict[str, Any]:
    """Validate policy records and enforce fixture/approval counts."""

    sources = policy.get("sources")
    if not isinstance(sources, list):
        raise SourcePolicyError("source policy must contain a sources list")
    if not FIXTURE_SOURCE_IDS <= {item.get("source_id") for item in sources if isinstance(item, dict)}:
        raise SourcePolicyError("all five required fixture sources must be registered")
    fixture_sources = [item for item in sources if item.get("source_id") in FIXTURE_SOURCE_IDS]
    if any(item.get("active") is not True for item in fixture_sources):
        raise SourcePolicyError("all fixture sources must be active")
    real_candidates = [item for item in sources if not item.get("fixture", False)]
    if len(real_candidates) < 5:
        raise SourcePolicyError("at least five real-source candidates must be registered")

    try:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(schema)
        for source in sources:
            errors = sorted(validator.iter_errors(source), key=lambda error: list(error.path))
            if errors:
                raise SourcePolicyError(
                    f"source {source.get('source_id', '<unknown>')} violates source schema: {errors[0].message}"
                )
    except SourcePolicyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ImportError) as exc:
        raise SourcePolicyError("could not load source schema") from exc
    return policy


def load_source_policy(
    path: str | Path = DEFAULT_SOURCE_POLICY,
    *,
    schema_path: str | Path = DEFAULT_SOURCE_SCHEMA,
) -> dict[str, Any]:
    return validate_source_policy(_read_yaml(Path(path)), schema_path=schema_path)


__all__ = [
    "DEFAULT_SOURCE_POLICY",
    "DEFAULT_SOURCE_SCHEMA",
    "FIXTURE_SOURCE_IDS",
    "SourcePolicyError",
    "load_source_policy",
    "validate_source_policy",
]
