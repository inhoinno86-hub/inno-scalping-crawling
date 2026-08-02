"""Small, explicit processing contracts used by Phase 0."""

from .state_machine import (
    ALLOWED_TRANSITIONS,
    ERROR_RETRY_FIELDS,
    TERMINAL_STATES,
    VALID_STATES,
    InvalidTransition,
    PipelineState,
    PipelineStateRecord,
    RetryMetadata,
    can_transition,
    is_terminal,
    transition,
)
from .source_policy import (
    DEFAULT_SOURCE_POLICY,
    DEFAULT_SOURCE_SCHEMA,
    FIXTURE_SOURCE_IDS,
    SourcePolicyError,
    load_source_policy,
    validate_source_policy,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "ERROR_RETRY_FIELDS",
    "TERMINAL_STATES",
    "VALID_STATES",
    "InvalidTransition",
    "PipelineState",
    "PipelineStateRecord",
    "RetryMetadata",
    "can_transition",
    "is_terminal",
    "transition",
    "DEFAULT_SOURCE_POLICY",
    "DEFAULT_SOURCE_SCHEMA",
    "FIXTURE_SOURCE_IDS",
    "SourcePolicyError",
    "load_source_policy",
    "validate_source_policy",
]
