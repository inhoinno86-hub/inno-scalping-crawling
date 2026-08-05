#!/usr/bin/env python3
"""Build offline extraction fixture records for the current fixture sources.

Prompts embed a per-row ``document_version_id``, so a recording keyed only by
prompt hash dies with the database that produced it.  This script captures the
extraction prompts one offline cycle actually issues and writes records keyed
by :func:`scalping_briefing.llm.fixture.stable_prompt_key`, which names the
call by prompt version and document content hash instead.

It performs no network or provider call.  The emitted responses are authored
from the fixture document's own text: the one field the text supports is filled
verbatim and everything else stays ``unknown`` so the recording never invents
strategy detail the source does not contain.

Output is a records JSON for ``scripts/record_llm_fixtures.py``:

    python scripts/build_offline_extraction_fixtures.py --out records.json
    python scripts/record_llm_fixtures.py --input records.json \
        --mapping src/scalping_briefing/llm/fixtures/response-map.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import scalping_briefing.llm.fixture as fixture_module  # noqa: E402
from scalping_briefing.config import load_config  # noqa: E402
from scalping_briefing.llm.fixture import (  # noqa: E402
    DOCUMENT_VERSION_PLACEHOLDER,
    FixtureMappingMissingError,
    stable_prompt_key,
)
from scalping_briefing.models import Base  # noqa: E402
from scalping_briefing.orchestration.cycle import run_cycle  # noqa: E402


DEFAULT_QUOTE_MAX_CHARS = 300


class _PromptCapture:
    """Collect the prompts the pipeline issues without answering any.

    ``Settings`` has no slot for an injected client, so the capture happens at
    the fixture client itself: every call records its prompt and then fails
    the way an unrecorded prompt already fails, which the cycle isolates per
    document.  One pass therefore yields every extraction prompt.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __enter__(self) -> "_PromptCapture":
        self._original = fixture_module.FixtureLLMClient.complete
        capture = self

        def complete(self, prompt: str, **_kwargs: Any) -> Any:  # noqa: ANN001
            capture.prompts.append(prompt)
            raise FixtureMappingMissingError("capture-only run: no response recorded")

        fixture_module.FixtureLLMClient.complete = complete
        return self

    def __exit__(self, *_exception: Any) -> None:
        fixture_module.FixtureLLMClient.complete = self._original


def _prompt_payload(prompt: str) -> dict[str, Any]:
    marker = "INPUT_JSON:"
    return json.loads(prompt[prompt.index(marker) + len(marker) :])


_LIST_FIELDS = frozenset({"signal_inputs", "required_data"})

# Each core field is filled only from a sentence the document actually
# contains, so every recorded claim has a quote that is a real substring of
# the source text.  A field whose lead-in is absent stays unknown.
_FIELD_LEAD_INS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("core_hypothesis", ("The hypothesis is that",)),
    ("signal_inputs", ("The signals used are",)),
    ("entry_logic", ("Entry is taken when",)),
    ("exit_logic", ("The position is exited",)),
    ("required_data", ("The data required is",)),
    ("risk_notes", ("The risks noted are",)),
)


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    for line in text.replace("\\n", "\n").splitlines():
        buffer = ""
        for chunk in line.split(". "):
            buffer = chunk.strip()
            if buffer:
                parts.append(buffer if buffer.endswith(".") else f"{buffer}.")
    return parts


def _field_quotes(text: str, *, quote_max_chars: int) -> dict[str, str]:
    found: dict[str, str] = {}
    for sentence in _sentences(text):
        for field_name, lead_ins in _FIELD_LEAD_INS:
            if field_name in found:
                continue
            if any(sentence.startswith(lead_in) for lead_in in lead_ins):
                if sentence in text and len(sentence) <= quote_max_chars:
                    found[field_name] = sentence
    return found


def _authored_response(payload: dict[str, Any], *, quote_max_chars: int) -> dict[str, Any]:
    text = str(payload.get("text") or "")
    quotes = _field_quotes(text, quote_max_chars=quote_max_chars)
    content_hash = str(payload.get("content_hash") or "")
    title = str(payload.get("title") or "Fixture document").strip()
    candidate_id = f"fixture-candidate-{content_hash.removeprefix('sha256:')[:16]}"

    response: dict[str, Any] = {
        "candidate_id": candidate_id,
        "document_version_ids": [DOCUMENT_VERSION_PLACEHOLDER],
        "canonical_name": title[:80] or "Fixture candidate",
        "summary": f"Offline fixture candidate recorded from: {title[:80]}",
        "relevance_status": "relevant",
        "review_status": "needs_review",
        "source_confidence": 0.5,
        "extraction_confidence": 0.5,
    }
    field_status: dict[str, str] = {}
    evidence: list[dict[str, str]] = []
    for field_name, _lead_ins in _FIELD_LEAD_INS:
        quote = quotes.get(field_name)
        status = "explicit" if quote else "unknown"
        if field_name in _LIST_FIELDS:
            response[field_name] = [quote] if quote else None
        else:
            response[field_name] = quote
        response[f"{field_name}_status"] = status
        field_status[field_name] = status
        if quote:
            # No document_version_id here on purpose: the extractor defaults
            # it to the version being processed, so the recording stays valid
            # for any future ingestion of the same content.
            evidence.append(
                {
                    "field_name": field_name,
                    "quote": quote,
                    "section_or_locator": "Document body",
                }
            )
    response["field_status"] = field_status
    response["metadata"] = {"fixture_case": "offline-authored", "evidence": evidence}
    return response


def _capture_prompts() -> list[str]:
    """Run one throwaway offline cycle and return its extraction prompts."""

    settings = load_config()
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        engine = create_engine(f"sqlite:///{root / 'capture.db'}")
        Base.metadata.create_all(engine)
        session = Session(engine)
        try:
            with _PromptCapture() as capture:
                run_cycle(
                    session,
                    settings=settings,
                    alerts_dir=root / "alerts",
                    report_output_dir=root / "reports",
                )
        finally:
            session.close()
            engine.dispose()
    return capture.prompts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="records JSON to write")
    parser.add_argument(
        "--quote-max-chars", type=int, default=DEFAULT_QUOTE_MAX_CHARS
    )
    parser.add_argument("--recorded-at", default=None)
    arguments = parser.parse_args()

    prompts = _capture_prompts()
    recorded_at = arguments.recorded_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")

    mappings: dict[str, Any] = {}
    for prompt in prompts:
        key = stable_prompt_key(prompt)
        if key is None:
            continue
        payload = _prompt_payload(prompt)
        mappings[key] = {
            "recorded_at": recorded_at,
            "input_document_version_id": payload.get("document_version_id"),
            "content_hash": payload.get("content_hash"),
            "model_name": "fixture",
            "prompt_version": key.split(":")[1],
            "fixture_case": "offline-authored",
            "response": _authored_response(
                payload, quote_max_chars=arguments.quote_max_chars
            ),
        }

    arguments.out.write_text(
        json.dumps({"mappings": mappings}, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"captured {len(prompts)} prompts -> {len(mappings)} records: {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
