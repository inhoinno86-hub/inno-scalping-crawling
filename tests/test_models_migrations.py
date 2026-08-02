from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scalping_briefing.delivery.guard import (
    DeliveryHistory,
    ResendApprovalRequired,
    can_resend,
    make_idempotency_key,
    next_attempt_no,
)
from scalping_briefing.models import (
    Base,
    Briefing,
    BriefingItem,
    Delivery,
    Document,
    DocumentVersion,
    Evidence,
    EvidenceValidationError,
    Source,
    StrategyCandidate,
    validate_briefing_item_evidence,
)


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PERSISTENCE_TABLES = {
    "sources",
    "documents",
    "document_versions",
    "collection_jobs",
    "evidence",
    "strategy_candidates",
    "reviews",
    "briefings",
    "briefing_items",
    "briefing_item_evidence",
    "deliveries",
    "llm_runs",
}


def test_initial_alembic_migration_creates_all_persistence_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(alembic_config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)
    assert EXPECTED_PERSISTENCE_TABLES <= set(inspector.get_table_names())

    delivery_constraints = inspector.get_unique_constraints("deliveries")
    assert {
        constraint["name"]
        for constraint in delivery_constraints
    } >= {"uq_deliveries_idempotency_key"}
    assert {
        (constraint["name"], tuple(constraint["column_names"]))
        for constraint in delivery_constraints
    } >= {("uq_deliveries_idempotency_key", ("idempotency_key",))}

    command.downgrade(alembic_config, "base")
    downgraded_engine = create_engine(f"sqlite:///{database_path}")
    assert not EXPECTED_PERSISTENCE_TABLES.intersection(
        inspect(downgraded_engine).get_table_names()
    )
    downgraded_engine.dispose()
    engine.dispose()


def test_delivery_idempotency_key_is_unique_in_sqlite() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        session.add(Briefing(briefing_id="briefing-1"))
        session.commit()
        session.add(
            Delivery.for_briefing(
                briefing_id="briefing-1",
                channel="telegram",
                content_hash="sha256-1",
                delivery_id="delivery-1",
            )
        )
        session.commit()

        session.add(
            Delivery.for_briefing(
                briefing_id="briefing-1",
                channel="telegram",
                content_hash="sha256-1",
                delivery_id="delivery-2",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_evidence_is_version_traceable_and_item_gate_is_persistent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    source = Source(
        source_id="source-1",
        name="Fixture",
        type="research",
        base_url="https://example.invalid",
        connector_type="rss",
    )
    document = Document(
        document_id="document-1",
        source=source,
        canonical_url="https://example.invalid/document-1",
        title="Fixture document",
    )
    version = DocumentVersion(
        document_version_id="version-1",
        document=document,
        content_hash="content-hash-1",
    )
    candidate = StrategyCandidate(
        candidate_id="candidate-1",
        canonical_name="Fixture strategy",
        summary="Bounded summary",
    )
    evidence = Evidence(
        evidence_id="evidence-1",
        document_version=version,
        strategy_candidate=candidate,
        field_name="summary",
        quote="bounded quote",
        section_or_locator="Abstract",
    )
    briefing = Briefing(briefing_id="briefing-1")
    item = BriefingItem(
        briefing_item_id="item-1",
        briefing=briefing,
        strategy_candidate=candidate,
        reason_included="new evidence",
        rank=1,
        evidence=[evidence],
    )

    validate_briefing_item_evidence(item)
    with Session(engine) as session:
        session.add_all([source, document, version, candidate, evidence, briefing, item])
        session.commit()
        persisted = session.get(Evidence, "evidence-1")
        assert persisted is not None
        assert persisted.document_version_id == "version-1"
        assert session.get(BriefingItem, "item-1").evidence[0].evidence_id == "evidence-1"

        missing_evidence = BriefingItem(
            briefing_item_id="item-2",
            briefing=briefing,
            strategy_candidate=candidate,
            reason_included="missing evidence",
            rank=2,
        )
        session.add(missing_evidence)
        with pytest.raises(EvidenceValidationError):
            session.commit()


def test_delivery_guard_rejects_success_resend_without_two_part_approval() -> None:
    history = DeliveryHistory(status="success", attempt_no=1)
    assert make_idempotency_key("briefing-1", "telegram", "hash-1") == (
        "briefing-1:telegram:hash-1"
    )
    assert can_resend(history) is False
    with pytest.raises(ResendApprovalRequired):
        next_attempt_no(history)

    assert can_resend(
        history,
        resend_reason="operator requested resend",
        resend_approved_by="reviewer-1",
    ) is True
    assert next_attempt_no(
        history,
        resend_reason="operator requested resend",
        resend_approved_by="reviewer-1",
    ) == 2
