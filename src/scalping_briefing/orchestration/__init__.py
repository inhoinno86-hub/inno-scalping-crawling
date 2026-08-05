"""Public contracts for briefing-cycle orchestration."""

from .collect import CollectionResult, collect_documents
from .cycle import (
    STAGE_NAMES,
    CycleSummary,
    StageFailure,
    StageTally,
    run_candidate_stages,
    run_cycle,
    run_stage,
)

__all__ = [
    "STAGE_NAMES",
    "CollectionResult",
    "collect_documents",
    "CycleSummary",
    "StageFailure",
    "StageTally",
    "run_candidate_stages",
    "run_cycle",
    "run_stage",
]
