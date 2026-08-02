from __future__ import annotations

import json
import socket
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scalping_briefing import run_briefing
from scalping_briefing.models import Base, Document
from scalping_briefing.net.robots import evaluate_robots
from scalping_briefing.pipeline.source_policy import load_source_policy


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "sources"


ROBOTS_FIXTURES = {
    "fixture_rss_blog": (
        "tests/fixtures/sources/fixture_rss_blog/robots.txt",
        "/research/rss",
    ),
    "fixture_atom_research": (
        "tests/fixtures/sources/fixture_atom_research/robots.txt",
        "/research/atom",
    ),
    "fixture_github_repo": (
        "tests/fixtures/sources/fixture_github_repo/robots.txt",
        "/repos/example/scalping-fixture",
    ),
    "fixture_paper_meta": (
        "tests/fixtures/sources/fixture_paper_meta/robots.txt",
        "/10.5555/fixture.scalping.2026",
    ),
}


def test_phase2_material_robots_allow_original_paths_and_disallow_other_paths() -> None:
    policy = load_source_policy()
    sources = {source["source_id"]: source for source in policy["sources"]}

    for source_id, (robots_path, allowed_path) in ROBOTS_FIXTURES.items():
        source = sources[source_id]
        assert source["metadata"]["robots_file"] == robots_path

        robots_text = (ROOT / robots_path).read_text(encoding="utf-8")
        assert any(
            line.strip().lower().startswith("disallow:")
            and line.split(":", 1)[1].strip()
            for line in robots_text.splitlines()
        )

        decision = evaluate_robots(
            robots_text,
            source["original_url"],
            user_agent=source["access_policy"]["user_agent"],
        )
        assert decision.allowed is True
        assert decision.robots_rule_matched == allowed_path
        assert urlsplit(source["original_url"]).path == allowed_path


def test_phase2_material_run_briefing_persists_four_sources_and_denies_exchange(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    def blocked_socket(*_args, **_kwargs):
        raise AssertionError("fixture collection attempted a socket call")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.sqlite3'}")
    monkeypatch.setenv("LLM_MODE", "fixture")
    monkeypatch.setenv("DELIVERY_MODE", "dry_run")

    assert run_briefing() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "dry_run"
    assert payload["llm_mode"] == "fixture"
    assert payload["delivery_mode"] == "dry_run"
    assert payload["active_fixture_sources"] == 5
    assert payload["sources"]["fixture_exchange_docs"]["access_denied"] == 1
    for source_id in ROBOTS_FIXTURES:
        assert payload["sources"][source_id]["access_denied"] == 0

    engine = create_engine(f"sqlite:///{tmp_path / 'app.sqlite3'}")
    try:
        with Session(engine) as session:
            documents = session.scalars(select(Document)).all()
            by_source = {source_id: [] for source_id in (*ROBOTS_FIXTURES, "fixture_exchange_docs")}
            for document in documents:
                by_source.setdefault(document.source_id, []).append(document)

            for source_id in ROBOTS_FIXTURES:
                assert by_source[source_id]
                for document in by_source[source_id]:
                    assert document.collection_status == "collected"
                    assert document.access_status == "allowed"
                    assert document.versions
                    for version in document.versions:
                        assert version.normalized_location is not None
                        assert Path(version.normalized_location).is_file()
                        assert Path(version.normalized_location).read_text(encoding="utf-8")

            exchange_documents = by_source["fixture_exchange_docs"]
            assert len(exchange_documents) == 1
            exchange = exchange_documents[0]
            assert exchange.collection_status == "access_denied"
            assert exchange.access_status == "denied"
            assert exchange.robots_allowed is False
            assert exchange.robots_rule_matched == "/private"
            assert len(exchange.versions) == 1
            exchange_version = exchange.versions[0]
            assert exchange_version.normalized_location is None
            assert exchange_version.raw_location is None
    finally:
        engine.dispose()
