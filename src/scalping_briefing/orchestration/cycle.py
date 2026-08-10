"""Single-shot briefing-cycle contracts and candidate-stage wiring."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

from .. import alerts
from ..delivery.connector import TelegramDryRunConnector, TelegramLiveConnector
from ..delivery.service import deliver_briefing
from ..logging_setup import mask_secrets
from ..ops.alerting import emit_metric_alerts
from ..ops.metrics import ObservationWindow, compute_all_metrics
from ..ops.report import archive_report, render_report
from ..pipeline.classify import classify_document
from ..pipeline.evidence_link import link_evidence
from ..pipeline.extract import extract_strategy_candidate
from ..pipeline.novelty import classify_novelty
from ..pipeline.routing import route_candidate
from ..pipeline.scoring import score_candidate
from ..pipeline.validate import validate_extracted_candidate
from ..pipeline.schedule import next_occurrence, schedule_trigger
from ..publishing.briefing_build import build_briefing
from ..publishing.briefing_gate import gate_briefing
from .collect import collect_documents


STAGE_NAMES = (
    "collect",
    "classify",
    "extract",
    "validate",
    "evidence",
    "score",
    "novelty",
    "route",
    "briefing",
    "gate",
    "delivery",
    "metrics",
    "report",
    "alerting",
)

_DEFAULT_TIMEZONE = "Asia/Seoul"
_DEFAULT_SCHEDULE = ("TUE 08:00", "FRI 08:00")
_DEFAULT_REPORT_OUTPUT_DIR = Path("storage/ops-reports")
_EXPECTED_METRIC_IDS = ("M1", "M2", "M3", "M4", "M5", "M6")
_MAX_FAILURE_REASON_CHARS = 200
_CLASSIFIABLE_STATES = frozenset(
    {"collected", "normalized", "deduplicated", "classified"}
)
_SECRET_SETTING_NAMES = frozenset(
    {
        "bottoken",
        "chatid",
        "reviewapitoken",
        "telegrambottoken",
        "telegramchatid",
        "telegramtoken",
        "token",
    }
)

ResultT = TypeVar("ResultT")
_MISSING = object()
_STAGE_FAILED = object()


def _safe_text(
    value: Any,
    *,
    limit: int = _MAX_FAILURE_REASON_CHARS,
    secret_values: Sequence[str] = (),
) -> str:
    """Return bounded text after applying the project's secret masker."""

    masked = mask_secrets(str(value), secret_values=secret_values)
    return str(masked)[:limit]


def _normalized_setting_name(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _settings_secret_values(settings: Any) -> set[str]:
    """Collect delivery credential values without adding configuration keys."""

    values: set[str] = set()
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if value is None or isinstance(value, (str, bytes, bytearray, int, float, bool)):
            return
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if _normalized_setting_name(key) in _SECRET_SETTING_NAMES:
                    if isinstance(nested, (str, int, float)) and str(nested):
                        values.add(str(nested))
                visit(nested)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for nested in value:
                visit(nested)
            return
        try:
            attributes = vars(value)
        except TypeError:
            return
        visit(attributes)

    visit(settings)
    for name, environment_value in os.environ.items():
        if _normalized_setting_name(name) in _SECRET_SETTING_NAMES and environment_value:
            values.add(environment_value)
    return values


def _summary_secret_values(summary: "CycleSummary") -> tuple[str, ...]:
    values = getattr(summary, "_secret_values", ())
    return tuple(str(value) for value in values if value)


def _setting(settings: Any, name: str, default: Any = None) -> Any:
    if settings is None:
        return default
    if isinstance(settings, Mapping):
        try:
            value = settings.get(name, default)
        except (AttributeError, KeyError):
            return default
        return default if value is None else value
    try:
        value = getattr(settings, name)
    except AttributeError:
        return default
    return default if value is None else value


def _setting_kwargs(settings: Any, *names: str) -> dict[str, Any]:
    """Return only non-null optional arguments exposed by ``settings``."""

    if settings is None:
        return {}
    values: dict[str, Any] = {}
    for name in names:
        try:
            value = (
                settings.get(name, _MISSING)
                if isinstance(settings, Mapping)
                else getattr(settings, name)
            )
        except (AttributeError, KeyError):
            continue
        if value is not _MISSING and value is not None:
            values[name] = value
    return values


def _stage_payload(value: Any) -> dict[str, int]:
    if isinstance(value, StageTally):
        return value.to_payload()
    if isinstance(value, Mapping):
        return {
            "processed": int(value.get("processed", 0)),
            "succeeded": int(value.get("succeeded", 0)),
            "failed": int(value.get("failed", 0)),
            "skipped": int(value.get("skipped", 0)),
        }
    return StageTally().to_payload()


@dataclass(frozen=True)
class StageFailure:
    """Bounded, masked description of one isolated stage failure."""

    stage: str
    identifier: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", str(self.stage))
        object.__setattr__(self, "identifier", _safe_text(self.identifier))
        object.__setattr__(self, "reason", _safe_text(self.reason))

    def to_payload(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "identifier": self.identifier,
            "reason": self.reason,
        }


@dataclass
class StageTally:
    """Counters for work observed by one orchestration stage.

    ``skipped`` counts inputs the stage was never asked to handle because
    they were already past it.  A skip is not a failure: it keeps a repeated
    cycle quiet instead of re-reporting finished work.
    """

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    def to_payload(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
        }


@dataclass
class CycleSummary:
    """Deterministically serializable result of one cycle invocation."""

    phase: str = "4b"
    status: str = "success"
    llm_mode: str = "fixture"
    delivery_mode: str = "dry_run"
    scheduled_for: str | None = None
    trigger_type: str | None = None
    briefing_id: str | None = None
    stages: dict[str, StageTally] = field(
        default_factory=lambda: {name: StageTally() for name in STAGE_NAMES}
    )
    briefing_generated: bool = False
    delivery_invoked: bool = False
    delivery_status: str | None = None
    metrics: dict[str, str] = field(default_factory=dict)
    report_path: str | None = None
    alerts_written: list[str] = field(default_factory=list)
    failures: list[StageFailure] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Keep every fixed stage present, including stages not wired yet.
        normalized = {
            name: (
                value
                if isinstance(value, StageTally)
                else StageTally(
                    processed=int(value.get("processed", 0)),
                    succeeded=int(value.get("succeeded", 0)),
                    failed=int(value.get("failed", 0)),
                    skipped=int(value.get("skipped", 0)),
                )
                if isinstance(value, Mapping)
                else StageTally()
            )
            for name, value in self.stages.items()
            if name in STAGE_NAMES
        }
        for name in STAGE_NAMES:
            normalized.setdefault(name, StageTally())
        self.stages = {name: normalized[name] for name in STAGE_NAMES}
        self.alerts_written = [str(path) for path in self.alerts_written]
        self.failures = [
            failure
            if isinstance(failure, StageFailure)
            else StageFailure(
                stage=str(failure.get("stage", "unknown")),
                identifier=str(failure.get("identifier", "cycle")),
                reason=str(failure.get("reason", "unknown failure")),
            )
            for failure in self.failures
        ]
        if self.failures and self.status == "success":
            self.status = "partial_success"

    @property
    def exit_code(self) -> int:
        """Return process status: only an entirely successful cycle is zero."""

        return 0 if self.status == "success" else 1

    def to_payload(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status,
            "llm_mode": self.llm_mode,
            "delivery_mode": self.delivery_mode,
            "scheduled_for": self.scheduled_for,
            "trigger_type": self.trigger_type,
            "briefing_id": self.briefing_id,
            "stages": {
                name: _stage_payload(self.stages[name]) for name in STAGE_NAMES
            },
            "briefing_generated": self.briefing_generated,
            "delivery_invoked": self.delivery_invoked,
            "delivery_status": self.delivery_status,
            "metrics": dict(sorted(self.metrics.items())),
            "report_path": self.report_path,
            "alerts_written": sorted(self.alerts_written),
            "failures": [failure.to_payload() for failure in self.failures],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True)


def run_stage(
    summary: CycleSummary,
    stage: str,
    identifier: str,
    func: Callable[[], ResultT],
    *,
    alerts_dir: str | Path,
    default: ResultT | None = None,
) -> ResultT | None:
    """Run one callable, isolate errors, and update its stage tally."""

    if stage not in STAGE_NAMES:
        raise ValueError(f"unknown cycle stage: {stage!r}")

    tally = summary.stages[stage]
    tally.processed += 1
    try:
        result = func()
    except Exception as exc:
        tally.failed += 1
        secret_values = _summary_secret_values(summary)
        safe_identifier = _safe_text(identifier, secret_values=secret_values)
        safe_reason = _safe_text(exc, secret_values=secret_values)
        summary.failures.append(
            StageFailure(
                stage=stage,
                identifier=safe_identifier,
                reason=safe_reason,
            )
        )
        if summary.status == "success":
            summary.status = "partial_success"

        try:
            details = mask_secrets(
                {
                    "stage": stage,
                    "identifier": safe_identifier,
                    "reason": safe_reason,
                },
                secret_values=secret_values,
            )
            artifact = alerts.record_failure(
                event=f"cycle.{stage}",
                message=str(
                    mask_secrets(
                        f"{stage} failed for {safe_identifier}: {safe_reason}",
                        secret_values=secret_values,
                    )
                ),
                details=details,
                alerts_dir=alerts_dir,
            )
        except Exception:
            # Failure reporting cannot turn an isolated stage failure into a
            # cycle-level exception.
            artifact = None
        if artifact is not None:
            summary.alerts_written.append(str(artifact))
        return default

    tally.succeeded += 1
    return result


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _metric_results(value: Any) -> list[Any]:
    """Normalize the metric boundary without consuming it more than once."""

    if value is None or value is _STAGE_FAILED:
        return []
    if isinstance(value, Mapping):
        if "metric_id" in value:
            return [value]
        return list(value.values())
    if isinstance(value, (str, bytes, bytearray)):
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _document_identifier(document_version: Any) -> str:
    for name in ("document_version_id", "version_id", "document_id", "id"):
        value = _field(document_version, name)
        if value is not _MISSING and value is not None:
            text = str(value).strip()
            if text:
                return text
    return "document-version"


def _processing_state(value: Any) -> str | None:
    for name in ("processing_status", "state"):
        current = _field(value, name)
        if current is not _MISSING and current is not None:
            return str(getattr(current, "value", current))
    return None


def _candidate_from_result(value: Any) -> Any | None:
    candidate = _field(value, "candidate")
    if candidate is not _MISSING and candidate is not None:
        return candidate
    candidate = _field(value, "strategy_candidate")
    if candidate is not _MISSING and candidate is not None:
        return candidate
    if _field(value, "candidate_id") is not _MISSING:
        return value
    return None


def _classification_payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    return {
        "status": _field(value, "status", "unknown"),
        "reason": _field(value, "reason", {}),
    }


def _is_relevant(value: Any) -> bool:
    status = _field(value, "status")
    if status is _MISSING or status is None:
        status = _field(value, "decision")
    return str(getattr(status, "value", status)) == "relevant"


def _result_is_valid(value: Any) -> bool:
    if value is None:
        return False
    error_class = _field(value, "error_class")
    if error_class is not _MISSING and error_class is not None:
        return False
    valid = _field(value, "valid")
    if valid is not _MISSING and valid is False:
        return False
    return _candidate_from_result(value) is not None


def _delivery_mode(settings: Any) -> str:
    configured = _setting(settings, "DELIVERY_MODE", "dry_run")
    environment = os.environ.get("DELIVERY_MODE")
    values = [value for value in (configured, environment) if value is not None]
    if any(str(value).strip().lower() != "dry_run" for value in values):
        return "live"
    return "dry_run"


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return fallback
        if parsed.tzinfo is None and fallback.tzinfo is not None:
            return parsed.replace(tzinfo=fallback.tzinfo)
        return parsed
    return fallback


def _observation_window(
    briefing: Any,
    *,
    scheduled_for: datetime,
    settings: Any,
    supplied: Any = None,
) -> ObservationWindow:
    if supplied is not None:
        return supplied
    window_start = _as_datetime(
        _field(briefing, "window_start"),
        scheduled_for - timedelta(days=14),
    )
    window_end = _as_datetime(
        _field(briefing, "window_end"),
        scheduled_for,
    )
    return ObservationWindow(
        start=window_start,
        end=window_end,
        timezone=str(_setting(settings, "TIMEZONE", _DEFAULT_TIMEZONE)),
    )


def _run_operational_stages(
    session: Any,
    *,
    briefing: Any,
    scheduled_for: datetime,
    settings: Any,
    summary: CycleSummary,
    alerts_dir: str | Path,
    report_output_dir: str | Path,
    observation_window: Any = None,
) -> None:
    """Run metrics, report, and alerting even after a cycle-level failure."""

    window = _observation_window(
        briefing,
        scheduled_for=scheduled_for,
        settings=settings,
        supplied=observation_window,
    )
    metrics = run_stage(
        summary,
        "metrics",
        "cycle",
        lambda: compute_all_metrics(
            session,
            window,
            settings=settings,
            delivery_mode=_setting(settings, "DELIVERY_MODE", summary.delivery_mode),
        ),
        alerts_dir=alerts_dir,
    )
    metric_results = _metric_results(metrics)
    metric_verdicts = {
        metric_id: "insufficient_data" for metric_id in _EXPECTED_METRIC_IDS
    }
    for metric in metric_results:
        identifier = _field(metric, "metric_id")
        if identifier is _MISSING or identifier is None:
            continue
        metric_id = str(identifier).strip().upper()
        if metric_id not in metric_verdicts:
            continue
        verdict = _field(metric, "verdict", "insufficient_data")
        if verdict is _MISSING or verdict is None:
            verdict = "insufficient_data"
        metric_verdicts[metric_id] = str(getattr(verdict, "value", verdict))
    summary.metrics = dict(sorted(metric_verdicts.items()))

    report_path = run_stage(
        summary,
        "report",
        "cycle",
        lambda: _render_and_archive_report(
            window,
            metric_results,
            settings=settings,
            delivery_mode=summary.delivery_mode,
            output_dir=report_output_dir,
        ),
        alerts_dir=alerts_dir,
    )
    if report_path is not None:
        summary.report_path = str(report_path)

    paths = run_stage(
        summary,
        "alerting",
        "cycle",
        lambda: emit_metric_alerts(
            window,
            metric_results,
            alerts_dir=alerts_dir,
        ),
        alerts_dir=alerts_dir,
        default=[],
    )
    if paths:
        summary.alerts_written.extend(str(path) for path in paths)
    summary.alerts_written.sort()


def _render_and_archive_report(
    window: Any,
    metrics: Any,
    *,
    settings: Any,
    delivery_mode: str,
    output_dir: str | Path,
) -> Path:
    rendered = render_report(
        window,
        metrics,
        settings=settings,
        delivery_mode=delivery_mode,
    )
    return archive_report(rendered, output_dir=output_dir)


def _document_versions(value: Any) -> list[Any]:
    """Normalize collection results from the public list-shaped boundary."""

    if value is None or value is _STAGE_FAILED:
        return []
    if isinstance(value, Mapping):
        value = value.get("document_versions", value.get("versions", []))
    else:
        nested = getattr(value, "document_versions", _MISSING)
        if nested is not _MISSING:
            value = nested
    if value is None or isinstance(value, (str, bytes, bytearray)):
        return []
    try:
        return list(value)
    except TypeError:
        return [value]


def _evidence_entries(value: Any) -> list[dict[str, Any]]:
    raw = _field(value, "evidence")
    if raw is _MISSING or raw is None:
        raw_response = _field(value, "raw_response")
        metadata = _field(raw_response, "metadata")
        raw = _field(metadata, "evidence")
    if raw is _MISSING or raw is None:
        return []
    if isinstance(raw, Mapping):
        if "field_name" in raw or "quote" in raw:
            return [dict(raw)]
        for key in ("accepted_quotes", "accepted_evidence", "evidence", "quotes"):
            if key in raw:
                return _evidence_entries(SimpleNamespace(evidence=raw[key]))
        return []
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        entries: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, Mapping):
                entries.append(dict(item))
                continue
            entry = {
                name: _field(item, name)
                for name in (
                    "evidence_id",
                    "document_version_id",
                    "strategy_candidate_id",
                    "field_name",
                    "quote",
                    "section_or_locator",
                    "captured_at",
                    "source_url",
                    "metadata",
                )
            }
            entries.append({name: item for name, item in entry.items() if item is not _MISSING})
        return [entry for entry in entries if entry.get("field_name") and entry.get("quote")]
    return []


def _existing_candidates(session: Any) -> list[Any]:
    if session is None or not hasattr(session, "scalars"):
        return []
    try:
        from sqlalchemy import select

        from ..models import StrategyCandidate

        return list(session.scalars(select(StrategyCandidate)).all())
    except Exception:
        # Existing candidates are comparison context, not a reason to abort
        # the isolated document pipeline when a lightweight test session is
        # supplied.
        return []


def _validated_payload_result(payload: Any) -> Any:
    if payload is _MISSING or payload is None:
        raise ValueError("validated extraction is missing validated_payload")
    if _candidate_from_result(payload) is None:
        raise ValueError("validated extraction payload is missing candidate_id")
    return payload


def _is_classifiable(document_version: Any) -> bool:
    """Mirror the classifier's own precondition on the version state.

    ``classify_document`` advances ``collected``/``normalized`` to
    ``deduplicated`` and accepts ``classified``; anything else raises
    ``InvalidTransition``.  An unset state belongs to a freshly built record
    and stays eligible.
    """

    state = _field(document_version, "processing_status", None)
    if state is None:
        return True
    return str(state) in _CLASSIFIABLE_STATES


def _invalid_validation_state(state: str | None) -> Any:
    raise ValueError(
        "candidate validation requires extracted or validated state, "
        f"got {state!r}"
    )


def run_candidate_stages(
    session: Any,
    document_versions: Sequence[Any],
    *,
    settings: Any,
    summary: CycleSummary,
    alerts_dir: str | Path,
    llm_client: Any = None,
    now: datetime | None = None,
) -> list[Any]:
    """Run candidate stages 2--8 for each document version in order.

    Downstream calls are made only when the preceding stage returned the
    input it requires.  This keeps skipped tallies at zero and lets
    :func:`run_stage` isolate failures per document and stage.

    ``llm_client`` is an opt-in override for the extraction stage.  When
    omitted (``None``), any ``llm_client`` already exposed on ``settings``
    (a test double, for example) is still honored via
    :func:`_setting_kwargs`, and if neither supplies one,
    ``extract_strategy_candidate`` falls back to its own default
    (``FixtureLLMClient``) -- unchanged from before this parameter existed.
    """

    routed: list[Any] = []
    for document_version in document_versions:
        identifier = _document_identifier(document_version)

        if not _is_classifiable(document_version):
            # Collection returns every ingested version, including rows an
            # earlier cycle already carried to a terminal state.  Re-running
            # classification on those is an invalid transition, not a
            # failure, so count the skip and move on.
            summary.stages["classify"].skipped += 1
            continue

        classification = run_stage(
            summary,
            "classify",
            identifier,
            lambda document_version=document_version: classify_document(
                document_version,
                session=session,
                use_llm=False,
            ),
            alerts_dir=alerts_dir,
        )
        if classification is None or not _is_relevant(classification):
            continue

        extract_kwargs = _setting_kwargs(settings, "llm_client", "quote_max_chars")
        if llm_client is not None:
            extract_kwargs["llm_client"] = llm_client
        extraction = run_stage(
            summary,
            "extract",
            identifier,
            lambda document_version=document_version, classification=classification, extract_kwargs=extract_kwargs: extract_strategy_candidate(
                document_version,
                session=session,
                classification=_classification_payload(classification),
                **extract_kwargs,
            ),
            alerts_dir=alerts_dir,
        )
        candidate = _candidate_from_result(extraction)
        if not _result_is_valid(extraction) or candidate is None:
            continue

        post_extraction_state = _processing_state(document_version)
        if post_extraction_state == "extracted":
            validation = run_stage(
                summary,
                "validate",
                identifier,
                lambda extraction=extraction: validate_extracted_candidate(
                    extraction,
                    **_setting_kwargs(settings, "quote_max_chars"),
                ),
                alerts_dir=alerts_dir,
            )
        elif post_extraction_state == "validated":
            validation = run_stage(
                summary,
                "validate",
                identifier,
                lambda extraction=extraction: _validated_payload_result(
                    _field(extraction, "validated_payload")
                ),
                alerts_dir=alerts_dir,
            )
        else:
            validation = run_stage(
                summary,
                "validate",
                identifier,
                lambda post_extraction_state=post_extraction_state: _invalid_validation_state(
                    post_extraction_state
                ),
                alerts_dir=alerts_dir,
            )
        validated_candidate = _candidate_from_result(validation)
        if not _result_is_valid(validation) or validated_candidate is None:
            continue
        candidate = validated_candidate

        evidence_entries = _evidence_entries(extraction)
        if not evidence_entries:
            evidence_entries = _evidence_entries(validation)
        if not evidence_entries:
            continue
        candidate_id = _field(candidate, "candidate_id")
        if candidate_id is _MISSING or candidate_id is None:
            continue
        evidence = run_stage(
            summary,
            "evidence",
            f"{identifier}:{candidate_id}",
            lambda document_version=document_version, candidate_id=str(candidate_id), evidence_entries=evidence_entries: link_evidence(
                document_version,
                candidate_id,
                evidence_entries,
                extraction_provenance={"accepted_quotes": evidence_entries},
                **_setting_kwargs(settings, "quote_max_chars"),
            ),
            alerts_dir=alerts_dir,
        )
        if not evidence:
            continue

        existing_candidates = _existing_candidates(session)
        score = run_stage(
            summary,
            "score",
            f"{identifier}:{candidate_id}",
            lambda document_version=document_version, candidate=candidate: score_candidate(
                candidate,
                document_version,
                existing_candidates,
                now=now,
            ),
            alerts_dir=alerts_dir,
        )
        if score is None:
            continue

        novelty = run_stage(
            summary,
            "novelty",
            f"{identifier}:{candidate_id}",
            lambda candidate=candidate, existing_candidates=existing_candidates: classify_novelty(
                candidate,
                existing_candidates,
                persist=True,
            ),
            alerts_dir=alerts_dir,
        )
        if novelty is None:
            continue

        score_value = _field(score, "value_score")
        if score_value is _MISSING:
            score_value = _field(score, "score")
        extraction_confidence = _field(candidate, "extraction_confidence")
        route_kwargs: dict[str, Any] = {
            "value_score": None if score_value is _MISSING else score_value,
            "extraction_confidence": (
                None
                if extraction_confidence is _MISSING
                else extraction_confidence
            ),
        }
        if settings is not None:
            route_kwargs["settings"] = settings
        route_kwargs.update(
            _setting_kwargs(
                settings,
                "candidate_score_threshold",
                "extraction_confidence_min",
            )
        )
        route_result = run_stage(
            summary,
            "route",
            f"{identifier}:{candidate_id}",
            lambda candidate=candidate, document_version=document_version, route_kwargs=route_kwargs: route_candidate(
                candidate,
                document_version,
                **route_kwargs,
            ),
            alerts_dir=alerts_dir,
        )
        if route_result is not None:
            routed.append(route_result)

    return routed


def run_cycle(
    session: Any,
    *,
    settings: Any,
    scheduled_for: datetime | None = None,
    trigger_type: str = "scheduled",
    connector: Any = None,
    alerts_dir: str | Path | None = None,
    report_output_dir: str | Path | None = None,
    observation_window: Any = None,
    now: datetime | None = None,
    run_attempt: int = 1,
    llm_client: Any = None,
    registry: Any = None,
) -> CycleSummary:
    """Run collection, candidate processing, briefing, gate, delivery, ops.

    ``llm_client`` is an opt-in override forwarded to the extraction stage
    (see :func:`run_candidate_stages`).  Leaving it unset reproduces prior
    behavior exactly.

    ``registry`` is an opt-in override forwarded to the collect stage (see
    :func:`collect_documents`).  Leaving it unset reproduces prior behavior
    exactly.
    """

    if alerts_dir is None:
        alerts_dir = _setting(settings, "alerts_dir", "alerts/")
    if report_output_dir is None:
        report_output_dir = _DEFAULT_REPORT_OUTPUT_DIR

    if scheduled_for is None:
        scheduled_for = next_occurrence(
            now or datetime.now(UTC),
            schedule=_setting(settings, "WEEKLY_REPORT_SCHEDULE", _DEFAULT_SCHEDULE),
            timezone=_setting(settings, "TIMEZONE", _DEFAULT_TIMEZONE),
        )
    if not isinstance(scheduled_for, datetime):
        raise TypeError("scheduled_for must be a datetime")

    trigger = schedule_trigger(scheduled_for, trigger_type=trigger_type)
    summary = CycleSummary(
        llm_mode=str(_setting(settings, "LLM_MODE", "fixture")),
        delivery_mode=_delivery_mode(settings),
        scheduled_for=trigger["scheduled_for"].isoformat(),
        trigger_type=trigger_type,
        briefing_id=str(trigger["briefing_id"]),
        alerts_written=[],
        failures=[],
    )
    summary._secret_values = tuple(sorted(_settings_secret_values(settings)))

    # Preserve the stage-free contract used by callers that only ask for the
    # deterministic schedule summary.  A real cycle always injects a session.
    if session is None:
        return summary

    collected = run_stage(
        summary,
        "collect",
        "cycle",
        lambda: collect_documents(
            session,
            settings=settings,
            registry=registry,
            storage_root=Path("storage"),
        ),
        alerts_dir=alerts_dir,
        default=[],
    )
    document_versions = _document_versions(collected)
    run_candidate_stages(
        session,
        document_versions,
        settings=settings,
        summary=summary,
        alerts_dir=alerts_dir,
        llm_client=llm_client,
        now=now,
    )

    briefing = run_stage(
        summary,
        "briefing",
        summary.briefing_id or "cycle",
        lambda: build_briefing(
            session,
            scheduled_for=scheduled_for,
            trigger_type=trigger_type,
            settings=settings,
            run_attempt=run_attempt,
        ),
        alerts_dir=alerts_dir,
    )
    if briefing is not None:
        summary.briefing_generated = True
        summary.briefing_id = str(
            _field(briefing, "briefing_id", summary.briefing_id)
        )

    gated = None
    gate_succeeded = False
    if briefing is not None:
        gated = run_stage(
            summary,
            "gate",
            summary.briefing_id or "cycle",
            lambda: gate_briefing(
                briefing,
                settings=settings,
                delivery_history=_field(briefing, "deliveries", []),
            ),
            alerts_dir=alerts_dir,
            default=_STAGE_FAILED,
        )
        gate_succeeded = gated is not _STAGE_FAILED

    if briefing is not None and gate_succeeded:
        delivery_result = run_stage(
            summary,
            "delivery",
            summary.briefing_id or "cycle",
            lambda: _deliver_once(
                session,
                briefing,
                connector=connector,
                settings=settings,
                summary=summary,
            ),
            alerts_dir=alerts_dir,
        )
        if delivery_result is not None:
            result_status = _field(delivery_result, "status", None)
            summary.delivery_status = (
                None if result_status is None else str(result_status)
            )
        else:
            # ``deliver_briefing`` deliberately returns None for a valid
            # zero-item briefing.  Preserve that service contract; do not
            # invent a successful Delivery status.
            summary.delivery_status = None

    _run_operational_stages(
        session,
        briefing=briefing,
        scheduled_for=scheduled_for,
        settings=settings,
        summary=summary,
        alerts_dir=alerts_dir,
        report_output_dir=report_output_dir,
        observation_window=observation_window,
    )

    commit = getattr(session, "commit", None)
    if callable(commit):
        commit()
    return summary


def _delivery_connector_for_settings(settings: Any) -> Any:
    """Choose a connector to match ``DELIVERY_MODE``, defaulting to dry-run.

    Sole assembly point for :class:`TelegramLiveConnector`. Mirrors
    ``_llm_client_for_settings`` in ``scalping_briefing.__init__``: a
    fixture-default ``DELIVERY_MODE`` (or one absent from a lightweight
    settings double) keeps the prior default behavior unchanged.
    """

    if _delivery_mode(settings) == "live":
        return TelegramLiveConnector(settings=settings)
    return TelegramDryRunConnector(settings=settings)


def _deliver_once(
    session: Any,
    briefing: Any,
    *,
    connector: Any,
    settings: Any,
    summary: CycleSummary,
) -> Any:
    """Invoke delivery exactly once after the cycle gate succeeds."""

    selected_connector = connector
    if selected_connector is None:
        selected_connector = _delivery_connector_for_settings(settings)
    summary.delivery_invoked = True
    return deliver_briefing(
        session,
        briefing,
        connector=selected_connector,
        settings=settings,
    )


__all__ = [
    "STAGE_NAMES",
    "CycleSummary",
    "StageFailure",
    "StageTally",
    "run_candidate_stages",
    "run_cycle",
    "run_stage",
]
