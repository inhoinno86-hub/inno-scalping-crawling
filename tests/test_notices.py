from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_notices_document_and_briefing_contract_reference_exist() -> None:
    notices = (ROOT / "docs" / "notices.md").read_text(encoding="utf-8")
    assert "안전 고지" in notices
    assert "저작권 고지" in notices
    assert "투자 고지" in notices
    briefing_schema = json.loads((ROOT / "schemas" / "briefing.schema.json").read_text(encoding="utf-8"))
    reference = f"{briefing_schema.get('$comment', '')} {notices}"
    assert "docs/notices.md" in reference
    assert "notices" in briefing_schema["required"]
