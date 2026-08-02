from __future__ import annotations

import json
from xml.etree import ElementTree
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "sources"


def test_fixture_manifest_covers_five_static_source_kinds() -> None:
    manifest = json.loads((FIXTURE_ROOT / "fixture-manifest.json").read_text(encoding="utf-8"))
    assert manifest["network_access"] == "none"
    assert len(manifest["sources"]) == 5
    for entry in manifest["sources"]:
        metadata = json.loads((FIXTURE_ROOT / entry["metadata"]).read_text(encoding="utf-8"))
        assert metadata["source_id"] == entry["source_id"]
        assert metadata["captured_at"]
        assert metadata["original_url"].startswith("http")
        assert "rate_limit" in metadata
        for response in entry["responses"]:
            assert (FIXTURE_ROOT / response).is_file()


def _rss_item_bodies(path: Path) -> dict[tuple[str, str], tuple[tuple[str, tuple[tuple[str, str], ...], str | None], ...]]:
    root = ElementTree.parse(path).getroot()
    items: dict[tuple[str, str], tuple[tuple[str, tuple[tuple[str, str], ...], str | None], ...]] = {}
    for item in root.findall("./channel/item"):
        guid = item.findtext("guid", default="")
        title = item.findtext("title", default="")
        items[(guid, title)] = tuple(
            (child.tag, tuple(sorted(child.attrib.items())), child.text)
            for child in item
        )
    return items


def test_fixture_manifest_records_v1_v2_capture_metadata() -> None:
    manifest = json.loads((FIXTURE_ROOT / "fixture-manifest.json").read_text(encoding="utf-8"))
    expected_paths = {
        "fixture_rss_blog/response.xml",
        "fixture_rss_blog/response.v2.xml",
        "fixture_atom_research/response.xml",
        "fixture_atom_research/response.v2.xml",
        "fixture_atom_research/headers.json",
        "fixture_atom_research/headers.v2.json",
        "fixture_github_repo/releases.json",
        "fixture_github_repo/releases.v2.json",
    }
    captures = {entry["path"]: entry for entry in manifest["captures"]}
    assert set(captures) == expected_paths
    for path, entry in captures.items():
        assert (FIXTURE_ROOT / path).is_file()
        assert entry["version"] in {"v1", "v2"}
        assert entry["captured_at"]
        assert entry["original_url"].startswith("http")
        metadata = json.loads(
            (FIXTURE_ROOT / entry["source_id"] / "metadata.json").read_text(encoding="utf-8")
        )
        assert entry["original_url"] == metadata["original_url"]


def test_rss_v2_has_one_changed_item_and_one_new_item() -> None:
    v1 = _rss_item_bodies(FIXTURE_ROOT / "fixture_rss_blog" / "response.xml")
    v2 = _rss_item_bodies(FIXTURE_ROOT / "fixture_rss_blog" / "response.v2.xml")
    common = set(v1) & set(v2)
    assert len(v2) == len(v1) + 1
    assert len(set(v2) - set(v1)) == 1
    assert sum(v1[key] != v2[key] for key in common) == 1


def test_atom_v2_has_new_conditional_request_headers() -> None:
    v1 = json.loads((FIXTURE_ROOT / "fixture_atom_research" / "headers.json").read_text(encoding="utf-8"))
    v2 = json.loads((FIXTURE_ROOT / "fixture_atom_research" / "headers.v2.json").read_text(encoding="utf-8"))
    assert v2["ETag"] != v1["ETag"]
    assert v2["Last-Modified"] != v1["Last-Modified"]
    assert v2["ETag"]
    assert v2["Last-Modified"]


def test_github_v2_has_new_commit_sha_and_release() -> None:
    v1 = json.loads((FIXTURE_ROOT / "fixture_github_repo" / "releases.json").read_text(encoding="utf-8"))
    v2 = json.loads((FIXTURE_ROOT / "fixture_github_repo" / "releases.v2.json").read_text(encoding="utf-8"))
    v1_ids = {release["id"] for release in v1}
    v2_ids = {release["id"] for release in v2}
    v1_commits = {release["target_commitish"] for release in v1}
    v2_commits = {release["target_commitish"] for release in v2}
    assert len(v2) == len(v1) + 1
    assert len(v2_ids - v1_ids) == 1
    assert v2_commits - v1_commits


def test_fixture_types_and_robots_disallow_case_are_static() -> None:
    rss = (FIXTURE_ROOT / "fixture_rss_blog" / "response.xml").read_text(encoding="utf-8")
    atom = (FIXTURE_ROOT / "fixture_atom_research" / "response.xml").read_text(encoding="utf-8")
    github = json.loads((FIXTURE_ROOT / "fixture_github_repo" / "releases.json").read_text(encoding="utf-8"))
    html = (FIXTURE_ROOT / "fixture_exchange_docs" / "response.html").read_text(encoding="utf-8")
    robots = (FIXTURE_ROOT / "fixture_exchange_docs" / "robots.txt").read_text(encoding="utf-8")
    paper = json.loads((FIXTURE_ROOT / "fixture_paper_meta" / "response.json").read_text(encoding="utf-8"))
    assert "<rss" in rss and rss.count("<item>") == 3
    assert "<feed" in atom and "<entry>" in atom
    assert github[0]["target_commitish"]
    assert "<script>" in html and "IGNORE ALL PREVIOUS INSTRUCTIONS" in html
    assert "Disallow: /private" in robots
    assert paper["message"]["DOI"]
