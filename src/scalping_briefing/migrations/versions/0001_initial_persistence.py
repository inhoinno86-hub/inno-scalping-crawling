"""Immutable initial persistence schema.

Revision ID: 0001_initial_persistence
Revises:
Create Date: 2026-08-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    JSON,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)


revision = "0001_initial_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the complete initial schema with immutable explicit DDL."""

    is_sqlite = op.get_bind().dialect.name == "sqlite"

    # SQLite cannot ALTER TABLE to add a unique constraint.  Keep the
    # constraint inline for SQLite so its inspector reports the named
    # constraint; PostgreSQL receives the same constraint explicitly below.
    documents_unique = (
        [
            UniqueConstraint(
                "source_id",
                "canonical_url",
                name="uq_documents_source_canonical_url",
            )
        ]
        if is_sqlite
        else []
    )
    document_versions_unique = (
        [
            UniqueConstraint(
                "document_id",
                "content_hash",
                name="uq_document_versions_document_hash",
            )
        ]
        if is_sqlite
        else []
    )
    deliveries_unique = (
        [UniqueConstraint("idempotency_key", name="uq_deliveries_idempotency_key")]
        if is_sqlite
        else []
    )

    op.create_table(
        "sources",
        Column("source_id", String(255), nullable=False),
        Column("name", String(255), nullable=False),
        Column("type", String(100), nullable=False),
        Column("base_url", String(2048), nullable=False),
        Column("connector_type", String(100), nullable=False),
        Column("active", Boolean(), nullable=False),
        Column("access_policy", JSON(), nullable=False),
        Column("robots_allowed", JSON(), nullable=False),
        Column("robots_rule_matched", String(2048)),
        Column("robots_evaluated_at", DateTime(timezone=True)),
        Column("robots_checked_at", DateTime(timezone=True)),
        Column("access_decision_reason", Text()),
        Column("terms_reference", String(2048)),
        Column("license_notes", Text()),
        Column("rate_limit", JSON(), nullable=False),
        Column("schedule", JSON()),
        Column("last_success_at", DateTime(timezone=True)),
        Column("cursor", Text()),
        Column("trust_tier", String(32), nullable=False),
        Column("error_state", JSON()),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("source_id", name="pk_sources"),
    )

    op.create_table(
        "strategy_candidates",
        Column("candidate_id", String(255), nullable=False),
        Column("strategy_id", String(255)),
        Column("canonical_name", String(512), nullable=False),
        Column("aliases", JSON(), nullable=False),
        Column("summary", Text(), nullable=False),
        Column("asset_classes", JSON(), nullable=False),
        Column("market_types", JSON(), nullable=False),
        Column("strategy_families", JSON(), nullable=False),
        Column("holding_horizon", String(128)),
        Column("microstructure_level", String(128)),
        Column("tags", JSON(), nullable=False),
        Column("core_hypothesis", Text()),
        Column("core_hypothesis_status", String(32), nullable=False),
        Column("signal_inputs", JSON()),
        Column("signal_inputs_status", String(32), nullable=False),
        Column("entry_logic", Text()),
        Column("entry_logic_status", String(32), nullable=False),
        Column("exit_logic", Text()),
        Column("exit_logic_status", String(32), nullable=False),
        Column("required_data", JSON()),
        Column("required_data_status", String(32), nullable=False),
        Column("required_frequency", String(128)),
        Column("risk_notes", Text()),
        Column("risk_notes_status", String(32), nullable=False),
        Column("field_status", JSON(), nullable=False),
        Column("relevance_status", String(32), nullable=False),
        Column("review_status", String(32), nullable=False),
        Column("source_confidence", String(64)),
        Column("extraction_confidence", String(64)),
        Column("value_score", String(64)),
        Column("value_score_breakdown", JSON(), nullable=False),
        Column("novelty_status", String(32)),
        Column("related_strategy_ids", JSON(), nullable=False),
        Column("document_version_ids", JSON(), nullable=False),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("candidate_id", name="pk_strategy_candidates"),
        CheckConstraint(
            "relevance_status IN ('relevant', 'irrelevant', 'background_only', 'unknown')",
            name="ck_strategy_candidates_relevance_status",
        ),
        CheckConstraint(
            "review_status IN ('pending', 'needs_review', 'approved', 'rejected', 'archived')",
            name="ck_strategy_candidates_review_status",
        ),
        CheckConstraint(
            "source_confidence IS NULL OR (CAST(source_confidence AS NUMERIC) >= 0 AND CAST(source_confidence AS NUMERIC) <= 1)",
            name="ck_strategy_candidates_source_confidence",
        ),
        CheckConstraint(
            "extraction_confidence IS NULL OR (CAST(extraction_confidence AS NUMERIC) >= 0 AND CAST(extraction_confidence AS NUMERIC) <= 1)",
            name="ck_strategy_candidates_extraction_confidence",
        ),
        CheckConstraint(
            "value_score IS NULL OR (CAST(value_score AS NUMERIC) >= 0 AND CAST(value_score AS NUMERIC) <= 100)",
            name="ck_strategy_candidates_value_score",
        ),
    )

    op.create_table(
        "briefings",
        Column("briefing_id", String(255), nullable=False),
        Column("scheduled_for", DateTime(timezone=True), nullable=False),
        Column("trigger_type", String(32), nullable=False),
        Column("run_attempt", Integer(), nullable=False),
        Column("window_start", DateTime(timezone=True), nullable=False),
        Column("window_end", DateTime(timezone=True), nullable=False),
        Column("window_truncated", Boolean(), nullable=False),
        Column("run_status", String(32), nullable=False),
        Column("publication_status", String(32), nullable=False),
        Column("generated_at", DateTime(timezone=True), nullable=False),
        Column("shared_at", DateTime(timezone=True)),
        Column("timezone", String(64), nullable=False),
        Column("markdown_location", Text()),
        Column("source_summary", JSON(), nullable=False),
        Column("candidate_count", Integer(), nullable=False),
        Column("approved_count", Integer(), nullable=False),
        Column("items_truncated", Integer(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("briefing_id", name="pk_briefings"),
        CheckConstraint(
            "trigger_type IN ('scheduled', 'manual')",
            name="ck_briefings_trigger_type",
        ),
        CheckConstraint("run_attempt >= 1", name="ck_briefings_run_attempt"),
        CheckConstraint(
            "run_status IN ('pending', 'running', 'success', 'failed')",
            name="ck_briefings_run_status",
        ),
        CheckConstraint(
            "publication_status IN ('draft', 'pending_approval', 'approved', 'rejected', 'published', 'archived')",
            name="ck_briefings_publication_status",
        ),
    )

    op.create_table(
        "documents",
        Column("document_id", String(255), nullable=False),
        Column("source_id", String(255), nullable=False),
        Column("canonical_url", String(2048), nullable=False),
        Column("original_url", String(2048)),
        Column("title", Text(), nullable=False),
        Column("author_or_org", String(512)),
        Column("published_at", DateTime(timezone=True)),
        Column("language", String(32)),
        Column("document_type", String(100)),
        Column("robots_allowed", JSON(), nullable=False),
        Column("robots_rule_matched", String(2048)),
        Column("robots_evaluated_at", DateTime(timezone=True)),
        Column("access_decision_reason", Text()),
        Column("collection_status", String(32), nullable=False),
        Column("processing_status", String(32), nullable=False),
        Column("access_status", String(16), nullable=False),
        Column("license", Text()),
        Column("content_hash", String(128)),
        Column("source_version_ref", String(512)),
        Column("first_collected_at", DateTime(timezone=True)),
        Column("last_checked_at", DateTime(timezone=True)),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("document_id", name="pk_documents"),
        ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_documents_source_id_sources",
        ),
        CheckConstraint(
            "collection_status IN ('discovered', 'collected', 'failed', 'access_denied')",
            name="ck_documents_collection_status",
        ),
        CheckConstraint(
            "processing_status IN ('discovered', 'collected', 'normalized', 'deduplicated', 'classified', 'extracted', 'validated', 'needs_review', 'approved', 'rejected', 'archived', 'duplicate', 'irrelevant', 'background_only', 'access_denied', 'failed')",
            name="ck_documents_processing_status",
        ),
        CheckConstraint(
            "access_status IN ('allowed', 'denied', 'unknown')",
            name="ck_documents_access_status",
        ),
        *documents_unique,
    )

    op.create_table(
        "collection_jobs",
        Column("collection_job_id", String(255), nullable=False),
        Column("source_id", String(255), nullable=False),
        Column("job_type", String(64), nullable=False),
        Column("status", String(32), nullable=False),
        Column("scheduled_for", DateTime(timezone=True)),
        Column("started_at", DateTime(timezone=True)),
        Column("completed_at", DateTime(timezone=True)),
        Column("attempt_no", Integer(), nullable=False),
        Column("cursor", Text()),
        Column("error_class", String(128)),
        Column("retry_count", Integer(), nullable=False),
        Column("next_retry_at", DateTime(timezone=True)),
        Column("last_error_at", DateTime(timezone=True)),
        Column("terminal_error", Boolean(), nullable=False),
        Column("error", Text()),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("collection_job_id", name="pk_collection_jobs"),
        ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_collection_jobs_source_id_sources",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed', 'cancelled')",
            name="ck_collection_jobs_status",
        ),
        CheckConstraint(
            "attempt_no >= 1", name="ck_collection_jobs_attempt_no"
        ),
        CheckConstraint(
            "retry_count >= 0", name="ck_collection_jobs_retry_count"
        ),
    )

    op.create_table(
        "document_versions",
        Column("document_version_id", String(255), nullable=False),
        Column("document_id", String(255), nullable=False),
        Column("version_no", Integer(), nullable=False),
        Column("retrieved_at", DateTime(timezone=True), nullable=False),
        Column("content_hash", String(128), nullable=False),
        Column("body_hash", String(128)),
        Column("source_version_ref", String(512)),
        Column("raw_location", Text()),
        Column("normalized_location", Text()),
        Column("change_summary", Text(), nullable=False),
        Column("collection_status", String(32), nullable=False),
        Column("processing_status", String(32), nullable=False),
        Column("access_status", String(16), nullable=False),
        Column("license", Text()),
        Column("robots_allowed", JSON(), nullable=False),
        Column("robots_rule_matched", String(2048)),
        Column("robots_evaluated_at", DateTime(timezone=True)),
        Column("access_decision_reason", Text()),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint(
            "document_version_id", name="pk_document_versions"
        ),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.document_id"],
            name="fk_document_versions_document_id_documents",
        ),
        CheckConstraint(
            "collection_status IN ('discovered', 'collected', 'failed', 'access_denied')",
            name="ck_document_versions_collection_status",
        ),
        CheckConstraint(
            "processing_status IN ('discovered', 'collected', 'normalized', 'deduplicated', 'classified', 'extracted', 'validated', 'needs_review', 'approved', 'rejected', 'archived', 'duplicate', 'irrelevant', 'background_only', 'access_denied', 'failed')",
            name="ck_document_versions_processing_status",
        ),
        CheckConstraint(
            "access_status IN ('allowed', 'denied', 'unknown')",
            name="ck_document_versions_access_status",
        ),
        *document_versions_unique,
    )

    op.create_table(
        "briefing_items",
        Column("briefing_item_id", String(255), nullable=False),
        Column("briefing_id", String(255), nullable=False),
        Column("strategy_candidate_id", String(255)),
        Column("strategy_id", String(255)),
        Column("reason_included", Text(), nullable=False),
        Column("rank", Integer(), nullable=False),
        Column("carried_over", Boolean(), nullable=False),
        Column("core_claim", Boolean(), nullable=False),
        Column("canonical_name", String(512)),
        Column("summary", Text()),
        Column("asset_classes", JSON(), nullable=False),
        Column("strategy_families", JSON(), nullable=False),
        Column("holding_horizon", String(128)),
        Column("value_score", String(64)),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("briefing_item_id", name="pk_briefing_items"),
        ForeignKeyConstraint(
            ["briefing_id"],
            ["briefings.briefing_id"],
            name="fk_briefing_items_briefing_id_briefings",
        ),
        ForeignKeyConstraint(
            ["strategy_candidate_id"],
            ["strategy_candidates.candidate_id"],
            name="fk_briefing_items_strategy_candidate_id_strategy_candidates",
        ),
        CheckConstraint(
            "strategy_candidate_id IS NOT NULL OR strategy_id IS NOT NULL",
            name="ck_briefing_items_strategy_target",
        ),
        CheckConstraint("rank >= 1", name="ck_briefing_items_rank"),
    )

    op.create_table(
        "evidence",
        Column("evidence_id", String(255), nullable=False),
        Column("document_version_id", String(255), nullable=False),
        Column("strategy_candidate_id", String(255), nullable=False),
        Column("field_name", String(128), nullable=False),
        Column("quote", Text(), nullable=False),
        Column("section_or_locator", Text(), nullable=False),
        Column("captured_at", DateTime(timezone=True), nullable=False),
        Column("source_url", String(2048)),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.document_version_id"],
            name="fk_evidence_document_version_id_document_versions",
        ),
        ForeignKeyConstraint(
            ["strategy_candidate_id"],
            ["strategy_candidates.candidate_id"],
            name="fk_evidence_strategy_candidate_id_strategy_candidates",
        ),
        CheckConstraint(
            "length(quote) BETWEEN 1 AND 300",
            name="ck_evidence_quote_length",
        ),
    )

    op.create_table(
        "reviews",
        Column("review_id", String(255), nullable=False),
        Column("strategy_candidate_id", String(255), nullable=False),
        Column("reviewer_id", String(255), nullable=False),
        Column("decision", String(32), nullable=False),
        Column("comment", Text()),
        Column("reviewed_at", DateTime(timezone=True), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("review_id", name="pk_reviews"),
        ForeignKeyConstraint(
            ["strategy_candidate_id"],
            ["strategy_candidates.candidate_id"],
            name="fk_reviews_strategy_candidate_id_strategy_candidates",
        ),
    )

    op.create_table(
        "deliveries",
        Column("delivery_id", String(255), nullable=False),
        Column("briefing_id", String(255), nullable=False),
        Column("channel", String(64), nullable=False),
        Column("idempotency_key", String(512), nullable=False),
        Column("content_hash", String(128)),
        Column("attempt_no", Integer(), nullable=False),
        Column("resend_reason", Text()),
        Column("resend_approved_by", String(255)),
        Column("attempted_at", DateTime(timezone=True), nullable=False),
        Column("status", String(32), nullable=False),
        Column("provider_reference", String(512)),
        Column("error", Text()),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("delivery_id", name="pk_deliveries"),
        ForeignKeyConstraint(
            ["briefing_id"],
            ["briefings.briefing_id"],
            name="fk_deliveries_briefing_id_briefings",
        ),
        CheckConstraint("attempt_no >= 1", name="ck_deliveries_attempt_no"),
        CheckConstraint(
            "status IN ('pending', 'success', 'failed', 'rejected')",
            name="ck_deliveries_status",
        ),
        CheckConstraint(
            "idempotency_key NOT LIKE ':%' "
            "AND idempotency_key NOT LIKE '%:' "
            "AND idempotency_key NOT LIKE '%::%' "
            "AND idempotency_key NOT LIKE '%:%:%:%' "
            "AND idempotency_key LIKE '%:%:%' "
            "AND idempotency_key NOT LIKE '% %'",
            name="ck_deliveries_idempotency_key_shape",
        ),
        CheckConstraint(
            "attempt_no < 2 OR (resend_reason IS NOT NULL AND trim(resend_reason) <> '' AND resend_approved_by IS NOT NULL AND trim(resend_approved_by) <> '')",
            name="ck_deliveries_resend_approval",
        ),
        *deliveries_unique,
    )

    op.create_table(
        "llm_runs",
        Column("llm_run_id", String(255), nullable=False),
        Column("run_type", String(64), nullable=False),
        Column("model_name", String(255), nullable=False),
        Column("prompt_version", String(255), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True)),
        Column("input_document_version_id", String(255)),
        Column("input_hash", String(128)),
        Column("output_hash", String(128)),
        Column("input_location", Text()),
        Column("output_location", Text()),
        Column("input_tokens", Integer()),
        Column("output_tokens", Integer()),
        Column("total_tokens", Integer()),
        Column("estimated_cost_usd", String(64)),
        Column("status", String(32), nullable=False),
        Column("error", Text()),
        Column("metadata", JSON(), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        PrimaryKeyConstraint("llm_run_id", name="pk_llm_runs"),
        ForeignKeyConstraint(
            ["input_document_version_id"],
            ["document_versions.document_version_id"],
            name="fk_llm_runs_input_document_version_id_document_versions",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'success', 'failed')",
            name="ck_llm_runs_status",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_llm_runs_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_llm_runs_output_tokens",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="ck_llm_runs_total_tokens",
        ),
    )

    op.create_table(
        "briefing_item_evidence",
        Column("briefing_item_id", String(255), nullable=False),
        Column("evidence_id", String(255), nullable=False),
        PrimaryKeyConstraint(
            "briefing_item_id",
            "evidence_id",
            name="pk_briefing_item_evidence",
        ),
        ForeignKeyConstraint(
            ["briefing_item_id"],
            ["briefing_items.briefing_item_id"],
            name="fk_briefing_item_evidence_briefing_item_id_briefing_items",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.evidence_id"],
            name="fk_briefing_item_evidence_evidence_id_evidence",
            ondelete="CASCADE",
        ),
    )

    if not is_sqlite:
        op.create_unique_constraint(
            "uq_documents_source_canonical_url",
            "documents",
            ["source_id", "canonical_url"],
        )
        op.create_unique_constraint(
            "uq_document_versions_document_hash",
            "document_versions",
            ["document_id", "content_hash"],
        )
        op.create_unique_constraint(
            "uq_deliveries_idempotency_key",
            "deliveries",
            ["idempotency_key"],
        )

    op.create_index(
        "ix_strategy_candidates_strategy_id",
        "strategy_candidates",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        "ix_briefing_items_briefing_id",
        "briefing_items",
        ["briefing_id"],
        unique=False,
    )
    op.create_index(
        "ix_briefing_items_strategy_candidate_id",
        "briefing_items",
        ["strategy_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_briefing_items_strategy_id",
        "briefing_items",
        ["strategy_id"],
        unique=False,
    )
    op.create_index(
        "ix_collection_jobs_source_id",
        "collection_jobs",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_deliveries_briefing_id",
        "deliveries",
        ["briefing_id"],
        unique=False,
    )
    op.create_index(
        "ix_documents_source_id",
        "documents",
        ["source_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_versions_document_id",
        "document_versions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_reviews_strategy_candidate_id",
        "reviews",
        ["strategy_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_document_version_id",
        "evidence",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_strategy_candidate_id",
        "evidence",
        ["strategy_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_llm_runs_input_document_version_id",
        "llm_runs",
        ["input_document_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop this revision's objects in dependency-safe reverse order."""

    is_sqlite = op.get_bind().dialect.name == "sqlite"

    if not is_sqlite:
        op.drop_constraint(
            "uq_deliveries_idempotency_key", "deliveries", type_="unique"
        )
        op.drop_constraint(
            "uq_document_versions_document_hash",
            "document_versions",
            type_="unique",
        )
        op.drop_constraint(
            "uq_documents_source_canonical_url", "documents", type_="unique"
        )

    op.drop_index("ix_llm_runs_input_document_version_id", table_name="llm_runs")
    op.drop_index("ix_evidence_strategy_candidate_id", table_name="evidence")
    op.drop_index("ix_evidence_document_version_id", table_name="evidence")
    op.drop_index(
        "ix_reviews_strategy_candidate_id", table_name="reviews"
    )
    op.drop_index(
        "ix_document_versions_document_id", table_name="document_versions"
    )
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_index("ix_deliveries_briefing_id", table_name="deliveries")
    op.drop_index("ix_collection_jobs_source_id", table_name="collection_jobs")
    op.drop_index("ix_briefing_items_strategy_id", table_name="briefing_items")
    op.drop_index(
        "ix_briefing_items_strategy_candidate_id", table_name="briefing_items"
    )
    op.drop_index("ix_briefing_items_briefing_id", table_name="briefing_items")
    op.drop_index(
        "ix_strategy_candidates_strategy_id", table_name="strategy_candidates"
    )

    op.drop_table("briefing_item_evidence")
    op.drop_table("llm_runs")
    op.drop_table("deliveries")
    op.drop_table("reviews")
    op.drop_table("evidence")
    op.drop_table("briefing_items")
    op.drop_table("document_versions")
    op.drop_table("collection_jobs")
    op.drop_table("documents")
    op.drop_table("briefings")
    op.drop_table("strategy_candidates")
    op.drop_table("sources")
