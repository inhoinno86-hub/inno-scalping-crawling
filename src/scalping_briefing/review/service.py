"""Read-only candidate views shared by review API and CLI callers.

The service owns database query and review-facing projection only.  It does
not render Markdown or manufacture publication content; the existing
``publishing.candidate_view`` adapter remains the publication contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, selectinload

from scalping_briefing.models import (
    Document,
    DocumentVersion,
    Evidence,
    Review,
    StrategyCandidate,
)
from scalping_briefing.models.base import utc_now
from scalping_briefing.pipeline import state_machine
from scalping_briefing.pipeline.validate import CORE_FIELDS
from scalping_briefing.publishing import candidate_view


def _text_link(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _column_payload(value: Any, *, metadata_name: str = "metadata") -> dict[str, Any]:
    """Copy mapped columns without exposing SQLAlchemy instrumentation."""

    result: dict[str, Any] = {}
    mapper = inspect(value).mapper
    for attribute in mapper.column_attrs:
        name = attribute.key
        output_name = metadata_name if name == "metadata_json" else name
        result[output_name] = deepcopy(getattr(value, name))
    return result


def _document_payload(document: Document | None) -> dict[str, Any] | None:
    if document is None:
        return None
    return _column_payload(document)


def _document_version_payload(
    version: DocumentVersion | None,
) -> dict[str, Any] | None:
    if version is None:
        return None
    result = _column_payload(version)
    result["document"] = _document_payload(version.document)
    return result


def _version_ids(candidate: StrategyCandidate) -> list[str]:
    values = candidate.document_version_ids or []
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(values, Sequence):
        return []
    return [str(value) for value in values if value is not None and str(value)]


def _first_link(
    versions: Iterable[DocumentVersion],
    evidence: Iterable[Evidence],
) -> str | None:
    for version in versions:
        document = version.document
        if document is not None:
            for value in (document.original_url, document.canonical_url):
                link = _text_link(value)
                if link is not None:
                    return link
    for row in evidence:
        link = _text_link(row.source_url)
        if link is not None:
            return link
    return None


def _candidate_payload(
    candidate: StrategyCandidate,
    source_link: str | None,
) -> dict[str, Any]:
    result = _column_payload(candidate)
    if source_link is not None:
        # These are source metadata fields consumed by candidate_view; they do
        # not become renderer prose.
        result["source_url"] = source_link
        result["source_link"] = source_link
    return result


def _evidence_payload(
    row: Evidence,
    *,
    source_link: str | None,
    version: DocumentVersion | None,
) -> dict[str, Any]:
    result = _column_payload(row)
    row_link = _text_link(row.source_url)
    if row_link is None:
        row_link = source_link
    if row_link is not None:
        result["source_url"] = row_link
        result["source_link"] = row_link
    result["document_version"] = _document_version_payload(version)
    return result


class ReviewService:
    """Query strategy candidates using a caller-owned SQLAlchemy session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_candidates(
        self,
        status: str | Any | None = None,
    ) -> list[StrategyCandidate]:
        """Return candidates, optionally restricted by ``review_status``."""

        statement = select(StrategyCandidate)
        if status is not None:
            status_value = getattr(status, "value", status)
            statement = statement.where(
                StrategyCandidate.review_status == status_value
            )
        statement = statement.order_by(
            StrategyCandidate.created_at,
            StrategyCandidate.candidate_id,
        )
        return list(self.session.scalars(statement).all())

    def record_decision(
        self,
        candidate_id: str,
        reviewer_id: str,
        decision: str,
        comment: str | None = None,
    ) -> Review:
        """Append a reviewer decision after validating its state transition."""

        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("reviewer_id must be a non-empty string")

        candidate = self.session.get(StrategyCandidate, candidate_id)
        if candidate is None:
            raise ValueError(f"strategy candidate not found: {candidate_id!r}")

        target = getattr(decision, "value", decision)
        # The existing state machine remains the sole transition authority.
        state_machine.transition(candidate.review_status, target)

        review = Review(
            strategy_candidate=candidate,
            reviewer_id=reviewer_id,
            decision=target,
            comment=comment,
        )
        candidate.review_status = target
        self.session.add(review)
        self.session.flush()
        return review

    def amend_field(
        self,
        candidate_id: str,
        reviewer_id: str,
        field_name: str,
        proposed_value: Any,
        reason: str,
    ) -> dict[str, Any]:
        """Append a metadata-only amendment for one core candidate field."""

        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("reviewer_id must be a non-empty string")
        if not isinstance(field_name, str) or field_name not in CORE_FIELDS:
            raise ValueError(
                f"field_name must be one of CORE_FIELDS: {field_name!r}"
            )

        candidate = self.session.get(StrategyCandidate, candidate_id)
        if candidate is None:
            raise ValueError(f"strategy candidate not found: {candidate_id!r}")

        current_metadata = candidate.metadata_json
        if current_metadata is None:
            metadata: dict[str, Any] = {}
        elif isinstance(current_metadata, dict):
            metadata = deepcopy(current_metadata)
        else:
            raise ValueError("candidate metadata_json must be a mapping")

        existing_amendments = metadata.get("review_amendments", [])
        if not isinstance(existing_amendments, list):
            raise ValueError("metadata_json['review_amendments'] must be a list")

        amendment = {
            "amended_at": utc_now().isoformat(),
            "reviewer_id": reviewer_id,
            "field_name": field_name,
            "previous_value": deepcopy(getattr(candidate, field_name)),
            "proposed_value": deepcopy(proposed_value),
            "reason": reason,
        }
        amendments = deepcopy(existing_amendments)
        amendments.append(amendment)
        metadata["review_amendments"] = amendments
        candidate.metadata_json = metadata
        self.session.flush()
        return amendment

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """Return one gated review view, or ``None`` when it does not exist.

        The view contains source/version metadata and bounded Evidence records.
        ``build_candidate_view`` is deliberately called before review metadata
        is attached, so the existing publication gate remains authoritative.
        """

        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return None

        statement = (
            select(StrategyCandidate)
            .where(StrategyCandidate.candidate_id == candidate_id.strip())
            .options(
                selectinload(StrategyCandidate.evidence)
                .selectinload(Evidence.document_version)
                .selectinload(DocumentVersion.document)
            )
        )
        candidate = self.session.scalar(statement)
        if candidate is None:
            return None

        evidence_rows = sorted(
            candidate.evidence,
            key=lambda row: (row.field_name, row.evidence_id),
        )
        requested_version_ids = _version_ids(candidate)
        versions_by_id: dict[str, DocumentVersion] = {}
        if requested_version_ids:
            version_statement = (
                select(DocumentVersion)
                .where(DocumentVersion.document_version_id.in_(requested_version_ids))
                .options(selectinload(DocumentVersion.document))
            )
            versions_by_id.update(
                {
                    version.document_version_id: version
                    for version in self.session.scalars(version_statement).all()
                }
            )
        for row in evidence_rows:
            if row.document_version is not None:
                versions_by_id.setdefault(
                    row.document_version.document_version_id,
                    row.document_version,
                )

        ordered_versions = [
            versions_by_id[version_id]
            for version_id in requested_version_ids
            if version_id in versions_by_id
        ]
        ordered_versions.extend(
            version
            for version_id, version in versions_by_id.items()
            if version_id not in requested_version_ids
        )
        source_link = _first_link(ordered_versions, evidence_rows)
        evidence_payloads = [
            _evidence_payload(
                row,
                source_link=source_link,
                version=versions_by_id.get(row.document_version_id)
                or row.document_version,
            )
            for row in evidence_rows
        ]

        publication_view = candidate_view.build_candidate_view(
            _candidate_payload(candidate, source_link),
            evidence_payloads,
        )
        primary_version = ordered_versions[0] if ordered_versions else None
        primary_version_payload = _document_version_payload(primary_version)

        # Keep the gated candidate-view shape at the top level while adding
        # non-rendered review metadata needed by API/CLI callers.
        result = dict(publication_view)
        result.update(
            {
                "candidate": _candidate_payload(candidate, source_link),
                "candidate_id": candidate.candidate_id,
                "review_status": candidate.review_status,
                "source_link": source_link,
                "source_url": source_link,
                "document_version": primary_version_payload,
                "document_version_id": (
                    primary_version.document_version_id
                    if primary_version is not None
                    else (
                        evidence_payloads[0].get("document_version_id")
                        if evidence_payloads
                        else None
                    )
                ),
                "document_versions": [
                    _document_version_payload(version) for version in ordered_versions
                ],
                "evidence": evidence_payloads,
            }
        )
        return result


__all__ = ["ReviewService"]
