"""Offline robots.txt parsing with an explicit fail-closed access result."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit
from typing import Any


RobotsClock = Callable[[], datetime | float | int]
RobotsAllowed = bool | str


def _timestamp(
    evaluated_at: datetime | None,
    clock: RobotsClock | None,
) -> datetime | None:
    if evaluated_at is not None:
        return evaluated_at
    if clock is None:
        return datetime.now(timezone.utc)
    value = clock()
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    raise TypeError("clock must return datetime or a numeric timestamp")


def _looks_like_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return bool(parsed.scheme and (parsed.netloc or parsed.scheme == "fixture"))


@dataclass(frozen=True, slots=True)
class RobotsDecision:
    """The four persisted access-decision fields plus convenience methods."""

    robots_allowed: RobotsAllowed
    robots_rule_matched: str | None
    robots_evaluated_at: datetime | None
    access_decision_reason: str

    def __post_init__(self) -> None:
        if not (
            type(self.robots_allowed) is bool or self.robots_allowed == "unknown"
        ):
            raise ValueError("robots_allowed must be true, false, or 'unknown'")
        if not isinstance(self.access_decision_reason, str) or not self.access_decision_reason:
            raise ValueError("access_decision_reason must be non-empty")

    @property
    def allowed(self) -> bool:
        """Return whether the decision permits a collection request."""

        return self.robots_allowed is True

    @property
    def access_allowed(self) -> bool:
        return self.allowed

    @property
    def can_access(self) -> bool:
        return self.allowed

    @property
    def rule_matched(self) -> str | None:
        return self.robots_rule_matched

    @property
    def evaluated_at(self) -> datetime | None:
        return self.robots_evaluated_at

    def __bool__(self) -> bool:
        return self.allowed

    def __getitem__(self, key: str) -> object:
        return self.as_dict()[key]

    def as_dict(self) -> dict[str, object]:
        return {
            "robots_allowed": self.robots_allowed,
            "robots_rule_matched": self.robots_rule_matched,
            "robots_evaluated_at": self.robots_evaluated_at,
            "access_decision_reason": self.access_decision_reason,
        }

    to_dict = as_dict


@dataclass(frozen=True, slots=True)
class _RobotsRule:
    allow: bool
    pattern: str
    order: int


@dataclass(slots=True)
class _RobotsGroup:
    agents: list[str]
    rules: list[_RobotsRule]


def _parse_robots(text: str) -> list[_RobotsGroup]:
    groups: list[_RobotsGroup] = []
    agents: list[str] = []
    rules: list[_RobotsRule] = []
    order = 0

    def flush() -> None:
        nonlocal agents, rules
        if agents:
            groups.append(_RobotsGroup(agents=agents, rules=rules))
        agents = []
        rules = []

    for raw_line in text.splitlines():
        line = raw_line.lstrip("\ufeff").split("#", 1)[0].strip()
        if not line:
            if agents:
                flush()
            continue
        field, separator, raw_value = line.partition(":")
        if not separator:
            continue
        field = field.strip().lower()
        value = raw_value.strip()
        if field == "user-agent":
            if rules:
                flush()
            if value:
                agents.append(value.lower())
            continue
        if field not in {"allow", "disallow"} or not agents:
            continue
        if not value:
            continue
        rules.append(
            _RobotsRule(allow=field == "allow", pattern=value, order=order)
        )
        order += 1
    flush()
    return groups


def _agent_score(group: _RobotsGroup, user_agent: str) -> int | None:
    lowered = user_agent.lower().strip() or "*"
    product = lowered.split("/", 1)[0].split()[0]
    scores: list[int] = []
    for declared in group.agents:
        if declared == "*":
            scores.append(0)
            continue
        declared_product = declared.split("/", 1)[0]
        if product == declared_product or lowered.startswith(declared):
            scores.append(len(declared))
    return max(scores) if scores else None


def _path_for_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("robots evaluation requires an absolute target URL")
    path = unquote(parsed.path or "/")
    return path + (f"?{parsed.query}" if parsed.query else "")


def _matches(pattern: str, path: str) -> bool:
    end_anchor = pattern.endswith("$")
    body = pattern[:-1] if end_anchor else pattern
    expression = re.escape(body).replace(r"\*", ".*")
    expression = "^" + expression + ("$" if end_anchor else "")
    return re.search(expression, path) is not None


def _unknown(
    reason: str,
    evaluated_at: datetime | None,
) -> RobotsDecision:
    return RobotsDecision(
        robots_allowed="unknown",
        robots_rule_matched=None,
        robots_evaluated_at=evaluated_at,
        access_decision_reason=reason,
    )


def evaluate_robots(
    robots_text: str | None = None,
    url: str | None = None,
    *,
    user_agent: str = "*",
    evaluated_at: datetime | None = None,
    clock: RobotsClock | None = None,
    required: bool = True,
    policy: str | None = None,
    robots_policy: str | None = None,
    robots_txt: str | None = None,
    target_url: str | None = None,
    response_text: str | None = None,
) -> RobotsDecision:
    """Evaluate one robots document for a target URL.

    Missing or unusable policy input yields ``unknown``.  Callers must check
    ``decision.allowed`` before requesting content.  The two common positional
    forms, ``(robots_text, url)`` and ``(url, robots_text)``, are accepted.
    """

    if robots_txt is not None:
        if robots_text is not None:
            raise TypeError("use robots_text or robots_txt, not both")
        robots_text = robots_txt
    if response_text is not None:
        if robots_text is not None:
            raise TypeError("use robots_text or response_text, not both")
        robots_text = response_text
    if target_url is not None:
        if url is not None:
            raise TypeError("use url or target_url, not both")
        url = target_url
    if url is not None and _looks_like_url(robots_text) and not _looks_like_url(url):
        robots_text, url = url, robots_text

    timestamp = _timestamp(evaluated_at, clock)
    selected_policy: object = (
        robots_policy if robots_policy is not None else policy
    )
    if isinstance(selected_policy, Mapping):
        nested_policy = selected_policy.get("access_policy")
        if isinstance(nested_policy, Mapping):
            selected_policy = nested_policy
        selected_policy = selected_policy.get(
            "robots",
            selected_policy.get("robots_policy", selected_policy.get("policy", "")),
        )
    elif selected_policy is not None and not isinstance(selected_policy, str):
        selected_policy = getattr(
            selected_policy,
            "robots",
            getattr(selected_policy, "robots_policy", selected_policy),
        )
    selected_policy = str(selected_policy or "").strip().lower()
    if selected_policy in {"not_applicable", "not applicable", "none"}:
        return RobotsDecision(
            robots_allowed=True,
            robots_rule_matched=None,
            robots_evaluated_at=timestamp,
            access_decision_reason="robots evaluation is not applicable by source policy",
        )
    if not required and robots_text is None:
        return RobotsDecision(
            robots_allowed=True,
            robots_rule_matched=None,
            robots_evaluated_at=timestamp,
            access_decision_reason="robots evaluation is not required by source policy",
        )
    if url is None:
        return _unknown("robots target URL is unavailable", timestamp)
    if robots_text is None:
        return _unknown("robots.txt could not be evaluated", timestamp)
    if not isinstance(robots_text, str):
        return _unknown("robots.txt content is not text", timestamp)
    try:
        path = _path_for_url(url)
        groups = _parse_robots(robots_text)
    except (TypeError, ValueError, UnicodeError):
        return _unknown("robots.txt could not be parsed safely", timestamp)

    scored = [(_agent_score(group, user_agent), group) for group in groups]
    matched_scores = [score for score, _group in scored if score is not None]
    if not matched_scores:
        return RobotsDecision(
            robots_allowed=True,
            robots_rule_matched=None,
            robots_evaluated_at=timestamp,
            access_decision_reason="no robots rule matched the requested user-agent",
        )

    best_score = max(matched_scores)
    matching_rules = [
        rule
        for score, group in scored
        if score == best_score
        for rule in group.rules
        if _matches(rule.pattern, path)
    ]
    if not matching_rules:
        return RobotsDecision(
            robots_allowed=True,
            robots_rule_matched=None,
            robots_evaluated_at=timestamp,
            access_decision_reason="robots.txt was evaluated and no path rule matched",
        )

    longest = max(len(rule.pattern) for rule in matching_rules)
    strongest = [rule for rule in matching_rules if len(rule.pattern) == longest]
    selected = next((rule for rule in strongest if rule.allow), strongest[0])
    if selected.allow:
        reason = f"robots allow rule matched path {selected.pattern}"
    else:
        reason = f"robots disallow rule matched path {selected.pattern}"
    return RobotsDecision(
        robots_allowed=selected.allow,
        robots_rule_matched=selected.pattern,
        robots_evaluated_at=timestamp,
        access_decision_reason=reason,
    )


class RobotsEvaluator:
    """Reusable evaluator carrying the source's user-agent and clock."""

    def __init__(
        self,
        *,
        user_agent: str = "*",
        clock: RobotsClock | None = None,
        required: bool = True,
        policy: str | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.clock = clock
        self.required = required
        self.policy = policy

    def evaluate(
        self,
        robots_text: str | None = None,
        url: str | None = None,
        **kwargs: Any,
    ) -> RobotsDecision:
        return evaluate_robots(
            robots_text,
            url,
            user_agent=kwargs.pop("user_agent", self.user_agent),
            clock=kwargs.pop("clock", self.clock),
            required=kwargs.pop("required", self.required),
            policy=kwargs.pop("policy", self.policy),
            **kwargs,
        )

    def evaluate_url(self, url: str, robots_text: str | None) -> RobotsDecision:
        return self.evaluate(robots_text, url)

    __call__ = evaluate


RobotsEvaluation = RobotsDecision
RobotsResult = RobotsDecision
RobotsPolicy = RobotsEvaluator


def access_is_allowed(decision: RobotsDecision | RobotsAllowed) -> bool:
    """Apply the repository's strict ``is True`` access rule."""

    if isinstance(decision, RobotsDecision):
        return decision.robots_allowed is True
    return decision is True


evaluate = evaluate_robots


__all__ = [
    "RobotsAllowed",
    "RobotsDecision",
    "RobotsEvaluation",
    "RobotsEvaluator",
    "RobotsPolicy",
    "RobotsResult",
    "access_is_allowed",
    "evaluate",
    "evaluate_robots",
]
