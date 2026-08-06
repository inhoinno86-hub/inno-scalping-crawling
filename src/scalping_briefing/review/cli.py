"""Offline command-line review interface.

The CLI opens the configured local database and delegates all review work to
``ReviewService``.  It intentionally has no HTTP, socket, or live-source
path.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import create_engine, inspect as sqlalchemy_inspect
from sqlalchemy.exc import NoInspectionAvailable
from sqlalchemy.orm import Session

from scalping_briefing.config import load_config
from scalping_briefing.review.service import ReviewService


def _mapped_payload(value: Any) -> dict[str, Any] | None:
    """Return mapped columns for an ORM object without its relationships."""

    try:
        mapper = sqlalchemy_inspect(value).mapper
    except (AttributeError, TypeError, NoInspectionAvailable):
        return None

    result: dict[str, Any] = {}
    for attribute in mapper.column_attrs:
        name = attribute.key
        output_name = "metadata" if name == "metadata_json" else name
        result[output_name] = getattr(value, name)
    return result


def _json_value(value: Any) -> Any:
    """Convert service results to JSON-compatible values."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_json_value(item) for item in value]

    mapped = _mapped_payload(value)
    if mapped is not None:
        return _json_value(mapped)
    return str(value)


def _json_default(value: Any) -> Any:
    return _json_value(value)


def _write_json(result: Any) -> None:
    print(
        json.dumps(
            _json_value(result),
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="review-cli",
        description="Inspect and decide local strategy review candidates.",
    )
    parser.add_argument(
        "--database-url",
        "--db",
        dest="database_url",
        help="SQLAlchemy database URL; defaults to configured DATABASE_URL.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list",
        help="list strategy candidates",
    )
    list_parser.add_argument(
        "--status",
        help="restrict candidates by review status",
    )

    show_parser = subparsers.add_parser(
        "show",
        help="show one candidate with source and Evidence",
    )
    show_parser.add_argument("candidate_id")

    decide_parser = subparsers.add_parser(
        "decide",
        help="record a review decision",
    )
    decide_parser.add_argument("candidate_id")
    decide_parser.add_argument(
        "reviewer_id_positional",
        nargs="?",
        help=argparse.SUPPRESS,
    )
    decide_parser.add_argument(
        "decision_positional",
        nargs="?",
        help=argparse.SUPPRESS,
    )
    decide_parser.add_argument(
        "--reviewer-id",
        "--reviewer_id",
        dest="reviewer_id",
        help="non-empty reviewer identifier",
    )
    decide_parser.add_argument(
        "--decision",
        dest="decision",
        help="approved, rejected, or archived",
    )
    decide_parser.add_argument("--comment")

    briefing_list_parser = subparsers.add_parser(
        "briefing-list",
        help="list briefings and their publication status",
    )
    briefing_list_parser.add_argument(
        "--publication-status",
        dest="publication_status",
        help="restrict briefings by publication status",
    )

    briefing_decide_parser = subparsers.add_parser(
        "briefing-decide",
        help="record an operator publication decision for one briefing",
    )
    briefing_decide_parser.add_argument("briefing_id")
    briefing_decide_parser.add_argument(
        "--reviewer-id",
        "--reviewer_id",
        dest="reviewer_id",
        help="non-empty reviewer identifier",
    )
    briefing_decide_parser.add_argument(
        "--decision",
        dest="decision",
        help="approved, rejected, or archived",
    )
    briefing_decide_parser.add_argument("--comment")

    briefing_deliver_parser = subparsers.add_parser(
        "briefing-deliver",
        help="gate and dry-run deliver one already-approved briefing",
    )
    briefing_deliver_parser.add_argument("briefing_id")
    briefing_deliver_parser.add_argument(
        "--resend-reason",
        dest="resend_reason",
        help="explicit reason recorded for a repeated delivery",
    )
    briefing_deliver_parser.add_argument(
        "--resend-approved-by",
        dest="resend_approved_by",
        help="reviewer who approved a repeated delivery",
    )
    return parser


def _deliver_briefing(session: Session, args: argparse.Namespace) -> Any:
    """Gate and dry-run deliver one already-built, operator-approved briefing.

    Delivery deliberately reuses the stored briefing instead of rebuilding it:
    a rebuild resets the publication status, because rebuilt content is not
    what the operator approved.
    """

    from scalping_briefing.delivery.connector import TelegramDryRunConnector
    from scalping_briefing.delivery.service import deliver_briefing
    from scalping_briefing.models import Briefing

    settings = load_config()
    mode = str(getattr(settings, "DELIVERY_MODE", "dry_run") or "dry_run")
    if mode != "dry_run":
        raise ValueError(
            "only DELIVERY_MODE=dry_run delivery is supported from the review CLI"
        )

    briefing = session.get(Briefing, args.briefing_id)
    if briefing is None:
        raise ValueError(f"briefing not found: {args.briefing_id!r}")

    # `deliver_briefing` runs the publication gate itself as the first
    # delivery-boundary call, on the payload it builds; gating the raw ORM row
    # here would check a different shape.
    delivery = deliver_briefing(
        session,
        briefing,
        connector=TelegramDryRunConnector(settings=settings),
        settings=settings,
        resend_reason=args.resend_reason,
        resend_approved_by=args.resend_approved_by,
    )
    if delivery is None:
        # An empty briefing is a valid report with no delivery target.
        return None
    # Mapped while the session is still open: the caller serializes after it
    # closes, and a detached ORM row cannot refresh itself.
    session.flush()
    return _mapped_payload(delivery)


def _database_url(value: str | None) -> str:
    if value is not None:
        return value
    return str(load_config().DATABASE_URL)


def _required_decision_value(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[str, str]:
    reviewer_id = args.reviewer_id or args.reviewer_id_positional
    decision = args.decision or args.decision_positional
    if not reviewer_id:
        parser.error("decide requires --reviewer-id")
    if not decision:
        parser.error("decide requires --decision")
    return reviewer_id, decision


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Any:
    engine = create_engine(_database_url(args.database_url))
    try:
        with Session(engine) as session:
            service = ReviewService(session)
            if args.command == "list":
                return {
                    "candidates": service.list_candidates(status=args.status),
                }
            if args.command == "show":
                result = service.get_candidate(args.candidate_id)
                return result if result is not None else {"candidate": None}
            if args.command == "briefing-list":
                return {
                    "briefings": service.list_briefings(
                        publication_status=args.publication_status
                    )
                }
            if args.command == "briefing-decide":
                reviewer_id, decision = _required_decision_value(parser, args)
                decided = service.record_briefing_decision(
                    args.briefing_id,
                    reviewer_id,
                    decision,
                    args.comment,
                )
                session.commit()
                return {"briefing_decision": decided}
            if args.command == "briefing-deliver":
                delivered = _deliver_briefing(session, args)
                session.commit()
                return {"delivery": delivered}

            reviewer_id, decision = _required_decision_value(parser, args)
            review = service.record_decision(
                args.candidate_id,
                reviewer_id,
                decision,
                args.comment,
            )
            session.commit()
            return {"review": review}
    finally:
        engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline review CLI and emit one JSON result to stdout."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _write_json(_run(args, parser))
    except (OSError, ValueError) as exc:
        _write_json({"error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by Python itself
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
