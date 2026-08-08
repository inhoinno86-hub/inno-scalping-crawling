"""Phase 2 §3.3 completion check: re-measure schema pass-rate against a live
local Ollama model.

This exercises the real ``LocalLLMClient`` (retry logic included) against
the same 5 fixture source documents used in the original ad-hoc benchmark,
then re-validates the parsed response through
``schema_guard.validate_strategy_candidate`` a second time (independent of
whatever ``LocalLLMClient.complete`` did internally) to get an honest
pass/fail per case.

Requires a live Ollama server at 127.0.0.1:11434 with
``qwen2.5:7b-instruct-q4_K_M`` pulled -- this is a pre-flight assumption
documented in intent-docs/scalping_local_llm_extraction_intent.md, not
something this test spins up. Marked ``integration`` so the default
``make test`` / ``pytest`` run (``addopts = "-m 'not integration'"``) skips
it automatically.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

from scalping_briefing.llm.local_ollama import LocalLLMClient
from scalping_briefing.llm.prompts import build_extraction_prompt
from scalping_briefing.llm.schema_guard import SchemaValidationError, validate_strategy_candidate

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXDIR = REPO_ROOT / "tests" / "fixtures" / "sources"
RESULT_MD_PATH = (
    REPO_ROOT
    / "to_do_prompts"
    / "local_llm_extraction_prompts"
    / "step_03_result.md"
)

CALL_TIMEOUT_SECONDS = 600.0


def _strip_tags(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _load_cases() -> list[dict]:
    """Mirror bench_local_llm.py's load_cases(): 5 fixture bodies -> case dicts."""

    cases: list[dict] = []

    rss = (FIXDIR / "fixture_rss_blog" / "response.xml").read_text()
    cases.append(
        {
            "id": "fixture_rss_blog",
            "title": "Spread Reversion Notes",
            "text": _strip_tags(rss),
            "url": "https://example.invalid/research/spread-reversion",
        }
    )

    atom = (FIXDIR / "fixture_atom_research" / "response.xml").read_text()
    cases.append(
        {
            "id": "fixture_atom_research",
            "title": "Order Flow Imbalance Study",
            "text": _strip_tags(atom),
            "url": "https://example.invalid/research/order-flow-imbalance",
        }
    )

    html = (FIXDIR / "fixture_exchange_docs" / "response.html").read_text()
    cases.append(
        {
            "id": "fixture_exchange_docs",
            "title": "Fixture Exchange Docs",
            "text": _strip_tags(html),
            "url": "https://example.invalid/exchange-docs",
        }
    )

    paper = json.loads((FIXDIR / "fixture_paper_meta" / "response.json").read_text())
    abstract = paper["message"]["abstract"]
    cases.append(
        {
            "id": "fixture_paper_meta",
            "title": paper["message"]["title"][0],
            "text": _strip_tags(abstract),
            "url": paper["message"]["URL"],
        }
    )

    readme = json.loads((FIXDIR / "fixture_github_repo" / "readme.json").read_text())
    content = base64.b64decode(readme["content"]).decode("utf-8")
    cases.append(
        {
            "id": "fixture_github_repo",
            "title": "Fixture strategy README",
            "text": _strip_tags(content),
            "url": readme["html_url"],
        }
    )

    return cases


def _build_prompt(case: dict) -> str:
    document_version = {
        "document_version_id": f"dv-{case['id']}",
        "canonical_url": case["url"],
        "title": case["title"],
        "language": "en",
        "document_type": "research",
        "metadata": {},
        "normalized_text": case["text"],
    }
    classification = {"relevance_status": "relevant"}
    return build_extraction_prompt(document_version, classification=classification)


def _write_result_report(results: list[dict]) -> None:
    passed = sum(1 for row in results if row["passed"])
    lines = [
        "# STEP 3 result: local LLM extraction pass-rate re-measurement",
        "",
        f"Pass rate: {passed}/{len(results)}",
        "",
        "| fixture id | pass/fail | call_count | failure reason |",
        "| --- | --- | --- | --- |",
    ]
    for row in results:
        status = "PASS" if row["passed"] else "FAIL"
        reason = row["reason"] if not row["passed"] else "-"
        lines.append(
            f"| {row['id']} | {status} | {row['call_count']} | {reason} |"
        )
    lines.append("")
    RESULT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_MD_PATH.write_text("\n".join(lines), encoding="utf-8")


@pytest.mark.integration
def test_local_llm_extraction_pass_rate_across_five_fixtures() -> None:
    cases = _load_cases()
    assert len(cases) == 5

    client = LocalLLMClient(timeout=CALL_TIMEOUT_SECONDS)
    results: list[dict] = []

    for case in cases:
        prompt = _build_prompt(case)
        parsed = client.complete(prompt)
        metadata = client.metadata(prompt)
        call_count = (metadata or {}).get("usage", {}).get("call_count")

        try:
            validate_strategy_candidate(parsed)
        except SchemaValidationError as exc:
            results.append(
                {
                    "id": case["id"],
                    "passed": False,
                    "reason": str(exc),
                    "call_count": call_count,
                }
            )
            continue

        results.append(
            {
                "id": case["id"],
                "passed": True,
                "reason": None,
                "call_count": call_count,
            }
        )

    assert len(results) == 5

    passed = sum(1 for row in results if row["passed"])
    print(f"\n=== PASS RATE: {passed}/{len(results)} ===\n")
    for row in results:
        status = "PASS" if row["passed"] else "FAIL"
        print(
            f"[{status}] {row['id']}: call_count={row['call_count']} "
            f"reason={row['reason']}"
        )

    _write_result_report(results)
