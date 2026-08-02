"""Phase 2 relevance classification and state recording."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from scalping_briefing.llm.audit import LLMCallResult, audited_complete
from scalping_briefing.llm.fixture import FixtureLLMClient
from scalping_briefing.llm.prompts import (
    CLASSIFICATION_PROMPT_VERSION,
    build_classification_prompt,
    document_payload,
)
from scalping_briefing.pipeline import state_machine


CLASSIFICATION_STATES = frozenset({"relevant", "irrelevant", "background_only"})
CLASSIFICATION_ERROR_CLASS = "classification_failed"

_MICROSTRUCTURE_TERMS = {
    "tick",
    "trade",
    "l1",
    "l2",
    "l3",
    "order book",
    "orderbook",
    "order-flow",
    "order flow",
    "spread",
    "queue",
    "imbalance",
    "microstructure",
    "market depth",
    "liquidity",
    "execution",
}
_STRATEGY_TERMS = {
    "scalp",
    "scalping",
    "short horizon",
    "short-horizon",
    "momentum",
    "mean reversion",
    "mean-reversion",
    "breakout",
    "arbitrage",
    "market making",
    "market-making",
    "entry",
    "exit",
    "signal",
    "holding period",
    "holding horizon",
}
_BACKGROUND_TERMS = {
    "market microstructure",
    "order book",
    "market depth",
    "liquidity",
    "price formation",
}
_IRRELEVANT_TERMS = {
    "long-term investment",
    "long term investment",
    "dividend",
    "portfolio allocation",
    "retirement",
    "valuation",
    "fundamental analysis",
}


class ClassificationResponseError(ValueError):
    """Raised when a classifier response has no valid decision."""

    error_class = "classification_response_invalid"


@dataclass(slots=True)
class ClassificationResult:
    """Decision, structured basis, and resulting document-version state."""

    status: str
    reason: dict[str, Any]
    processing_status: str
    document_version: Any
    prompt: str | None = None
    prompt_hash: str | None = None
    raw_response: Any = None
    llm_run: Any = None
    error_class: str | None = None

    @property
    def decision(self) -> str:
        return self.status

    @property
    def state(self) -> str:
        return self.processing_status

    @property
    def relevant(self) -> bool:
        return self.status == "relevant"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "processing_status": self.processing_status,
            "prompt_hash": self.prompt_hash,
            "error_class": self.error_class,
            "llm_run_id": getattr(self.llm_run, "llm_run_id", None),
        }


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _state(record: Any) -> str:
    value = _field(record, "processing_status")
    if value is None:
        value = _field(record, "state")
    if value is None:
        raise ValueError("document version processing_status is required")
    return str(getattr(value, "value", value))


def _set_state(record: Any, target: str) -> None:
    current = _state(record)
    if current == target:
        return
    # Explicit call is intentional: this is the only state mutation helper
    # used by this module.
    state_machine.transition(current, target)
    if isinstance(record, Mapping):
        try:
            record["processing_status"] = target  # type: ignore[index]
        except TypeError as exc:
            raise TypeError("document version mapping must be mutable") from exc
    else:
        setattr(record, "processing_status", target)


def _advance_to_deduplicated(record: Any) -> None:
    current = _state(record)
    if current == "collected":
        _set_state(record, "normalized")
        current = "normalized"
    if current == "normalized":
        _set_state(record, "deduplicated")
        current = "deduplicated"
    if current not in {"deduplicated", "classified"}:
        raise state_machine.InvalidTransition(
            f"classification requires deduplicated state, got {current!r}"
        )


def _metadata(record: Any) -> dict[str, Any]:
    value = _field(record, "metadata_json")
    if value is None:
        value = _field(record, "metadata", {})
    return dict(value) if isinstance(value, Mapping) else {}


def _store_metadata(record: Any, value: Mapping[str, Any]) -> None:
    if isinstance(record, Mapping):
        record["metadata"] = dict(value)  # type: ignore[index]
        if "metadata_json" in record:  # type: ignore[operator]
            record["metadata_json"] = dict(value)  # type: ignore[index]
        return
    setattr(record, "metadata_json", dict(value))


def _document_text(document_version: Any) -> tuple[str, str | None]:
    payload = document_payload(document_version)
    text = payload.get("text")
    return (str(text) if text is not None else "", payload.get("title"))


def _matches(text: str, terms: set[str]) -> list[str]:
    lowered = text.casefold()
    return sorted(term for term in terms if term.casefold() in lowered)


def _snippets(text: str, term: str) -> list[str]:
    # Store bounded context, not the full untrusted source body.
    match = re.search(re.escape(term), text, flags=re.IGNORECASE)
    if match is None:
        return []
    start = max(0, match.start() - 55)
    end = min(len(text), match.end() + 95)
    return [" ".join(text[start:end].split())[:180]]


def deterministic_relevance(
    document_text: str,
    *,
    title: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Classify without guessing from absent fields.

    Rules are deliberately conservative: a microstructure term alone is
    background; a strategy/decision term plus relevant data is a candidate.
    """

    combined = " ".join(part for part in (title or "", document_text) if part)
    micro = _matches(combined, _MICROSTRUCTURE_TERMS)
    strategy = _matches(combined, _STRATEGY_TERMS)
    background = _matches(combined, _BACKGROUND_TERMS)
    irrelevant = _matches(combined, _IRRELEVANT_TERMS)
    if irrelevant and not strategy:
        status = "irrelevant"
    elif strategy and (micro or any(term in combined.casefold() for term in ("seconds", "minutes"))):
        status = "relevant"
    elif micro or background:
        status = "background_only"
    else:
        status = "irrelevant"

    signals = []
    for name, values in (
        ("microstructure", micro),
        ("strategy", strategy),
        ("background", background),
        ("irrelevant", irrelevant),
    ):
        signals.append(
            {
                "signal": name,
                "matched": bool(values),
                "terms": values,
                "evidence_snippets": [
                    snippet
                    for term in values[:3]
                    for snippet in _snippets(combined, term)
                ],
            }
        )
    reason = {
        "version": 1,
        "decision": status,
        "rule": "deterministic_relevance_v1",
        "signals": signals,
        "basis": {
            "microstructure_terms": micro,
            "strategy_terms": strategy,
            "background_terms": background,
            "irrelevant_terms": irrelevant,
        },
    }
    return status, reason


def _llm_status(raw_response: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw_response, Mapping):
        raise ClassificationResponseError("classification response must be an object")
    value = raw_response.get("relevance_status", raw_response.get("status"))
    if value not in CLASSIFICATION_STATES:
        raise ClassificationResponseError(
            "classification response relevance_status is invalid"
        )
    raw_reason = raw_response.get("reason", raw_response.get("rationale"))
    reason: dict[str, Any] = {
        "version": 1,
        "decision": value,
        "rule": "llm_relevance_v1",
        "signals": raw_response.get("signals", []),
        "basis": raw_reason if isinstance(raw_reason, (str, Mapping, list)) else {},
    }
    return str(value), reason


def _failure_metadata(
    record: Any,
    *,
    error_class: str,
    error: Exception,
    llm_run: Any = None,
) -> None:
    metadata = _metadata(record)
    metadata["classification"] = {
        "status": "failed",
        "error_class": error_class,
        "error": str(error),
        "llm_run_id": getattr(llm_run, "llm_run_id", None),
    }
    metadata["error_class"] = error_class
    _store_metadata(record, metadata)


def classify_document(
    document_version: Any,
    *,
    session: Any = None,
    llm_client: Any = None,
    use_llm: bool = False,
    document_text: str | None = None,
    model_name: str = "fixture",
    prompt_version: str = CLASSIFICATION_PROMPT_VERSION,
    estimated_cost_usd: float = 0.0,
) -> ClassificationResult:
    """Classify one document version and persist its state/reason.

    ``use_llm`` is opt-in so deterministic offline classification remains
    useful for arbitrary local documents.  When a client is supplied, every
    call crosses :func:`audited_complete`.
    """

    _advance_to_deduplicated(document_version)
    text, title = _document_text(document_version)
    if document_text is not None:
        text = document_text
    prompt: str | None = None
    digest: str | None = None
    llm_run: Any = None
    raw_response: Any = None

    try:
        deterministic_status, deterministic_reason = deterministic_relevance(
            text,
            title=title,
        )
        if use_llm or llm_client is not None:
            client = llm_client or FixtureLLMClient()
            prompt = build_classification_prompt(
                document_version,
                document_text=text,
            )
            call: LLMCallResult = audited_complete(
                client,
                prompt,
                session=session,
                document_version=document_version,
                model_name=model_name,
                prompt_version=prompt_version,
                run_type="classification",
                estimated_cost_usd=estimated_cost_usd,
            )
            digest = call.prompt_hash
            llm_run = call.run
            raw_response = call.response
            status, reason = _llm_status(raw_response)
            reason["deterministic_reference"] = deterministic_reason
        else:
            status, reason = deterministic_status, deterministic_reason
    except ClassificationResponseError as exc:
        _set_state(document_version, "failed")
        _failure_metadata(
            document_version,
            error_class=exc.error_class,
            error=exc,
            llm_run=llm_run,
        )
        return ClassificationResult(
            status="failed",
            reason={"error_class": exc.error_class, "error": str(exc)},
            processing_status=_state(document_version),
            document_version=document_version,
            prompt=prompt,
            prompt_hash=digest,
            raw_response=raw_response,
            llm_run=llm_run,
            error_class=exc.error_class,
        )
    except Exception as exc:
        error_class = getattr(exc, "error_class", CLASSIFICATION_ERROR_CLASS)
        _set_state(document_version, "failed")
        _failure_metadata(
            document_version,
            error_class=error_class,
            error=exc,
            llm_run=getattr(exc, "llm_run", llm_run),
        )
        raise

    _set_state(document_version, "classified")
    target = "extracted" if status == "relevant" else status
    _set_state(document_version, target)
    metadata = _metadata(document_version)
    metadata["classification"] = {
        "status": status,
        "reason": reason,
        "prompt_hash": digest,
        "llm_run_id": getattr(llm_run, "llm_run_id", None),
    }
    metadata["classification_reason"] = reason
    _store_metadata(document_version, metadata)
    return ClassificationResult(
        status=status,
        reason=reason,
        processing_status=_state(document_version),
        document_version=document_version,
        prompt=prompt,
        prompt_hash=digest,
        raw_response=raw_response,
        llm_run=llm_run,
    )


def classify_document_version(*args: Any, **kwargs: Any) -> ClassificationResult:
    return classify_document(*args, **kwargs)


def classify(*args: Any, **kwargs: Any) -> ClassificationResult:
    return classify_document(*args, **kwargs)


classify_relevance = classify_document


__all__ = [
    "CLASSIFICATION_ERROR_CLASS",
    "CLASSIFICATION_STATES",
    "ClassificationResponseError",
    "ClassificationResult",
    "classify",
    "classify_document",
    "classify_document_version",
    "classify_relevance",
    "deterministic_relevance",
]
