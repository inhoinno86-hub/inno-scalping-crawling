from __future__ import annotations

import copy

from scalping_briefing.pipeline.source_policy import (
    FIXTURE_SOURCE_IDS,
    load_source_policy,
    validate_source_policy,
)


def test_source_policy_has_five_active_fixtures_and_five_real_candidates() -> None:
    policy = load_source_policy()
    sources = policy["sources"]
    by_id = {source["source_id"]: source for source in sources}
    assert FIXTURE_SOURCE_IDS <= by_id.keys()
    assert all(by_id[source_id]["active"] is True for source_id in FIXTURE_SOURCE_IDS)
    real_candidates = [source for source in sources if not source["fixture"]]
    assert len(real_candidates) >= 5
    required = {"robots_allowed", "robots_rule_matched", "robots_evaluated_at", "terms_reference", "license_notes", "rate_limit"}
    assert all(required <= source.keys() for source in sources)
    assert all("requests_per_minute" in source["rate_limit"] for source in sources)


def test_source_policy_allows_activating_a_real_candidate() -> None:
    policy = load_source_policy()
    activated = copy.deepcopy(policy)
    real_source = next(source for source in activated["sources"] if not source["fixture"])
    real_source["active"] = True
    validate_source_policy(activated)
