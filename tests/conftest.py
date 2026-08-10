from __future__ import annotations

import copy
from typing import Any

from scalping_briefing.pipeline.source_policy import load_source_policy
from scalping_briefing.sources import registry as _registry_module
from scalping_briefing.sources.registry import SourceRegistry


def fixture_only_policy() -> dict[str, Any]:
    """A source policy snapshot with every non-fixture candidate forced inactive.

    Offline tests must stay isolated from whatever real-source candidates an
    operator has flipped to ``active: true`` in the live
    ``config/source-policy.yaml`` — that file is production config, not test
    fixture data, and tests should keep passing regardless of its state.
    """

    policy = copy.deepcopy(load_source_policy())
    for source in policy["sources"]:
        if not source.get("fixture", False):
            source["active"] = False
    return policy


def isolate_source_policy(monkeypatch: Any) -> None:
    """Make ``SourceRegistry()`` (with no explicit policy) load a
    fixture-only snapshot, regardless of the live policy file's real-source
    active flags.

    ``run_briefing()`` and friends build their registry internally with no
    override hook (that entrypoint's source is a frozen contract, see
    ``tests/test_phase4b_entrypoint.py``), so isolation has to happen one
    level down: ``SourceRegistry.__init__`` resolves an omitted policy via
    the module-level ``load_source_policy`` name, and monkeypatching that
    name here redirects every such call for the duration of the test without
    touching any production source file.
    """

    snapshot = fixture_only_policy()
    monkeypatch.setattr(
        _registry_module,
        "load_source_policy",
        lambda *_args, **_kwargs: snapshot,
    )


def build_fixture_only_registry(**kwargs: Any) -> SourceRegistry:
    return SourceRegistry(policy=fixture_only_policy(), **kwargs)
