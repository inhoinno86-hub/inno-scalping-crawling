"""JSON Schema trust boundary for strategy-candidate LLM output."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STRATEGY_CANDIDATE_SCHEMA = (
    REPOSITORY_ROOT / "schemas" / "strategy_candidate.schema.json"
)


class SchemaValidationError(ValueError):
    """Raised when untrusted LLM output is not a strategy-candidate object."""

    error_class = "schema_validation_error"

    def __init__(self, message: str, *, errors: tuple[dict[str, Any], ...] = ()) -> None:
        super().__init__(message)
        self.errors = errors


class RawOutputError(SchemaValidationError):
    """Raised when a response cannot be decoded into a JSON object."""

    error_class = "raw_output_invalid"


@dataclass(frozen=True, slots=True)
class GuardedCandidate:
    """Validated copy of a raw response plus stable validation metadata."""

    value: dict[str, Any]
    schema_path: str
    error_class: str | None = None

    @property
    def valid(self) -> bool:
        return self.error_class is None


def _schema_path(path: str | Path | None) -> Path:
    selected = Path(path) if path is not None else DEFAULT_STRATEGY_CANDIDATE_SCHEMA
    if not selected.is_file():
        raise FileNotFoundError(f"strategy candidate schema not found: {selected}")
    return selected


def load_strategy_candidate_schema(
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load the existing repository schema without changing it."""

    selected = _schema_path(schema_path)
    try:
        payload = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchemaValidationError(
            f"unable to load strategy candidate schema: {selected}"
        ) from exc
    if not isinstance(payload, dict):
        raise SchemaValidationError("strategy candidate schema root must be an object")
    Draft202012Validator.check_schema(payload)
    return payload


def _decode_raw_output(raw_output: Any) -> dict[str, Any]:
    if isinstance(raw_output, Mapping):
        value = dict(raw_output)
    elif isinstance(raw_output, bytes):
        try:
            decoded = raw_output.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RawOutputError("LLM output is not UTF-8 JSON") from exc
        value = _decode_json_text(decoded)
    elif isinstance(raw_output, str):
        value = _decode_json_text(raw_output)
    else:
        raise RawOutputError("LLM output must be a JSON object")

    if not isinstance(value, dict):
        raise RawOutputError("LLM output JSON root must be an object")
    return value


def _decode_json_text(text: str) -> dict[str, Any]:
    candidate = text.strip()
    # A fenced response is accepted only as a transport wrapper.  The parsed
    # value still crosses the exact JSON Schema guard below.
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(candidate)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RawOutputError("LLM output is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RawOutputError("LLM output JSON root must be an object")
    return value


def _validation_errors(
    validator: Draft202012Validator,
    value: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    errors: list[dict[str, Any]] = []
    for error in sorted(validator.iter_errors(value), key=lambda item: tuple(item.path)):
        errors.append(
            {
                "path": ".".join(str(part) for part in error.path),
                "validator": error.validator,
                "message": error.message,
            }
        )
    return tuple(errors)


def validate_strategy_candidate(
    raw_output: Any,
    *,
    schema_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a copy only when raw output satisfies existing JSON Schema.

    No defaults, inferred values, or database identifiers are inserted here.
    That makes this function safe to use as the only input to candidate
    mapping.
    """

    schema = load_strategy_candidate_schema(schema_path)
    value = _decode_raw_output(raw_output)
    errors = _validation_errors(Draft202012Validator(schema), value)
    if errors:
        first = errors[0]
        detail = first["message"]
        if first["path"]:
            detail = f"{first['path']}: {detail}"
        raise SchemaValidationError(
            f"strategy candidate schema validation failed: {detail}",
            errors=errors,
        )
    return copy.deepcopy(value)


def guard_strategy_candidate(
    raw_output: Any,
    *,
    schema_path: str | Path | None = None,
) -> GuardedCandidate:
    """Non-throwing companion for callers that need a gate result object."""

    selected = _schema_path(schema_path)
    try:
        value = validate_strategy_candidate(raw_output, schema_path=selected)
    except SchemaValidationError as exc:
        return GuardedCandidate(
            value={},
            schema_path=str(selected),
            error_class=exc.error_class,
        )
    return GuardedCandidate(value=value, schema_path=str(selected))


def is_valid_strategy_candidate(
    raw_output: Any,
    *,
    schema_path: str | Path | None = None,
) -> bool:
    try:
        validate_strategy_candidate(raw_output, schema_path=schema_path)
    except (SchemaValidationError, FileNotFoundError):
        return False
    return True


# Compatibility names for pipeline adapters.
validate_candidate = validate_strategy_candidate
guard_candidate = guard_strategy_candidate
assert_valid_candidate = validate_strategy_candidate


__all__ = [
    "DEFAULT_STRATEGY_CANDIDATE_SCHEMA",
    "GuardedCandidate",
    "RawOutputError",
    "SchemaValidationError",
    "assert_valid_candidate",
    "guard_candidate",
    "guard_strategy_candidate",
    "is_valid_strategy_candidate",
    "load_strategy_candidate_schema",
    "validate_candidate",
    "validate_strategy_candidate",
]
