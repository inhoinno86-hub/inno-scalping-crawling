"""Audited provider-independent LLM calls.

The caller supplies an ``LLMClient``-compatible object.  This module records
the call before returning control to the extraction pipeline and records
provider, fixture, parsing, or quota failures as ``LLMRun(status='failed')``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any

from scalping_briefing.models import LLMRun
from scalping_briefing.models.base import utc_now

from .fixture import prompt_hash


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else str(value).encode("utf-8")
    return f"sha256:{sha256(payload).hexdigest()}"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _metadata(client: Any, prompt: str) -> Mapping[str, Any]:
    getter = getattr(client, "metadata", None)
    if not callable(getter):
        return {}
    try:
        value = getter(prompt)
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _usage_value(metadata: Mapping[str, Any], name: str) -> int | None:
    usage = metadata.get("usage")
    candidates: list[Any] = [metadata.get(name)]
    if isinstance(usage, Mapping):
        candidates.extend((usage.get(name), usage.get(name.removesuffix("_tokens"))))
    for value in candidates:
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _estimated_tokens(value: Any) -> int:
    # Deterministic fallback for fixture clients with no provider tokenizer.
    # It is an audit estimate, never a billing assertion.
    text = value if isinstance(value, str) else _json_bytes(value).decode("utf-8")
    return max(1, len(text.split())) if text else 0


def _cost_value(metadata: Mapping[str, Any], fallback: float) -> float:
    value = metadata.get("estimated_cost_usd", fallback)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0.0, parsed)


def _input_document_id(document_version: Any) -> str | None:
    if document_version is None:
        return None
    value = _field(document_version, "document_version_id")
    return str(value) if value is not None else None


def _save(session: Any, run: LLMRun) -> LLMRun:
    if session is not None:
        session.add(run)
        session.flush()
    return run


@dataclass(slots=True)
class LLMCallResult:
    """Response plus its persisted audit row."""

    response: Any
    run: LLMRun
    prompt_hash: str
    input_tokens: int
    output_tokens: int

    @property
    def raw_output(self) -> Any:
        return self.response

    @property
    def llm_run(self) -> LLMRun:
        return self.run


def record_llm_run(
    session: Any,
    *,
    model_name: str,
    prompt_version: str,
    started_at: datetime,
    input_document_version_id: str | None,
    status: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    estimated_cost_usd: float | None = 0.0,
    input_hash: str | None = None,
    output_hash: str | None = None,
    error: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    completed_at: datetime | None = None,
    run_type: str = "extraction",
) -> LLMRun:
    """Create and optionally flush one explicit ``LLMRun`` record."""

    if status not in {"pending", "running", "success", "failed"}:
        raise ValueError(f"unsupported LLM run status: {status!r}")
    run = LLMRun(
        run_type=run_type,
        model_name=model_name,
        prompt_version=prompt_version,
        started_at=started_at,
        completed_at=completed_at,
        input_document_version_id=input_document_version_id,
        input_hash=input_hash,
        output_hash=output_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=estimated_cost_usd,
        status=status,
        error=error,
        metadata=dict(metadata or {}),
    )
    return _save(session, run)


def audited_complete(
    client: Any,
    prompt: str,
    *,
    session: Any = None,
    document_version: Any = None,
    input_document_version_id: str | None = None,
    model_name: str = "fixture",
    prompt_version: str = "phase2-v1",
    run_type: str = "extraction",
    estimated_cost_usd: float = 0.0,
    **kwargs: Any,
) -> LLMCallResult:
    """Call one client and persist success or failure audit metadata."""

    if not isinstance(prompt, str):
        raise TypeError("LLM prompt must be a string")
    started_at = utc_now()
    digest = prompt_hash(prompt)
    version_id = input_document_version_id or _input_document_id(document_version)
    base_metadata = {
        "prompt_hash": digest,
        "input_document_version_id": version_id,
        "prompt_version": prompt_version,
        "run_type": run_type,
    }
    try:
        complete = getattr(client, "complete", None)
        if not callable(complete):
            if callable(client):
                raw_output = client(prompt, **kwargs)
            else:
                raise TypeError("LLM client must expose complete(prompt)")
        else:
            raw_output = complete(prompt, **kwargs)
    except Exception as exc:
        base_metadata["error_type"] = type(exc).__name__
        run = record_llm_run(
            session,
            model_name=model_name,
            prompt_version=prompt_version,
            started_at=started_at,
            completed_at=utc_now(),
            input_document_version_id=version_id,
            status="failed",
            input_tokens=_estimated_tokens(prompt),
            output_tokens=0,
            total_tokens=_estimated_tokens(prompt),
            estimated_cost_usd=estimated_cost_usd,
            input_hash=_sha256(prompt),
            error=str(exc),
            metadata=base_metadata,
            run_type=run_type,
        )
        # Preserve exact fixture/provider failure semantics for callers while
        # ensuring the failed row is already visible in the session.
        setattr(exc, "llm_run", run)
        raise

    recording_metadata = _metadata(client, prompt)
    input_tokens = _usage_value(recording_metadata, "input_tokens")
    output_tokens = _usage_value(recording_metadata, "output_tokens")
    total_tokens = _usage_value(recording_metadata, "total_tokens")
    if input_tokens is None:
        input_tokens = _estimated_tokens(prompt)
    if output_tokens is None:
        output_tokens = _estimated_tokens(raw_output)
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens
    cost = _cost_value(recording_metadata, estimated_cost_usd)
    base_metadata.update(
        {
            key: value
            for key, value in recording_metadata.items()
            if key not in {"response", "output", "completion"}
        }
    )
    base_metadata["input_tokens"] = input_tokens
    base_metadata["output_tokens"] = output_tokens
    base_metadata["total_tokens"] = total_tokens
    base_metadata["estimated_cost_usd"] = cost
    run = record_llm_run(
        session,
        model_name=str(recording_metadata.get("model_name", model_name)),
        prompt_version=str(recording_metadata.get("prompt_version", prompt_version)),
        started_at=started_at,
        completed_at=utc_now(),
        input_document_version_id=version_id,
        status="success",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost_usd=cost,
        input_hash=_sha256(prompt),
        output_hash=_sha256(_json_bytes(raw_output)),
        metadata=base_metadata,
        run_type=run_type,
    )
    return LLMCallResult(
        response=raw_output,
        run=run,
        prompt_hash=digest,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


call_with_audit = audited_complete
run_llm = audited_complete


class AuditedLLMClient:
    """Small ``LLMClient`` adapter that audits every ``complete`` call."""

    def __init__(
        self,
        client: Any,
        *,
        session: Any = None,
        document_version: Any = None,
        model_name: str = "fixture",
        prompt_version: str = "phase2-v1",
        run_type: str = "extraction",
        estimated_cost_usd: float = 0.0,
    ) -> None:
        self.client = client
        self.session = session
        self.document_version = document_version
        self.model_name = model_name
        self.prompt_version = prompt_version
        self.run_type = run_type
        self.estimated_cost_usd = estimated_cost_usd
        self.last_call: LLMCallResult | None = None

    def complete(self, prompt: str, **kwargs: Any) -> Any:
        self.last_call = audited_complete(
            self.client,
            prompt,
            session=self.session,
            document_version=self.document_version,
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            run_type=self.run_type,
            estimated_cost_usd=self.estimated_cost_usd,
            **kwargs,
        )
        return self.last_call.response


__all__ = [
    "AuditedLLMClient",
    "LLMCallResult",
    "audited_complete",
    "call_with_audit",
    "record_llm_run",
    "run_llm",
]
