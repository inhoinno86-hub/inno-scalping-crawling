"""Reject investment advice, trading signals, and return guarantees.

The lint is deliberately small and deterministic. It is a publication guard,
not a classifier: matching one phrase is enough to reject text.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final


PhraseCategory = str


@dataclass(frozen=True)
class BannedPhraseMatch:
    """A matched phrase and its location in the original text."""

    category: PhraseCategory
    phrase: str
    start: int
    end: int


class BannedPhraseError(ValueError):
    """Raised when publication text contains a prohibited phrase."""

    def __init__(self, match: BannedPhraseMatch) -> None:
        self.match = match
        super().__init__(
            f"banned publishing phrase ({match.category}): {match.phrase!r}"
        )


BANNED_PHRASES: Final[dict[PhraseCategory, tuple[str, ...]]] = {
    "investment_recommendation": (
        "투자 추천",
        "투자 권유",
        "매수 추천",
        "매도 추천",
        "매수 권유",
        "매도 권유",
        "investment recommendation",
        "investment advice",
        "buy recommendation",
        "sell recommendation",
        "buy tip",
        "sell tip",
    ),
    "trading_signal": (
        "매수 신호",
        "매도 신호",
        "매매 신호",
        "매수 타점",
        "매도 타점",
        "buy signal",
        "sell signal",
        "trading signal",
        "entry signal",
        "exit signal",
    ),
    "return_guarantee": (
        "수익 보장",
        "수익률 보장",
        "원금 보장",
        "무손실",
        "확정 수익",
        "고수익 보장",
        "guaranteed return",
        "guaranteed returns",
        "guaranteed profit",
        "guaranteed profits",
        "guaranteed income",
        "profit guarantee",
        "return guarantee",
        "profit guaranteed",
        "income guaranteed",
        "returns guaranteed",
        "risk-free",
        "risk free",
        "no-loss",
        "no loss",
        "principal guaranteed",
    ),
}


_PATTERNS: Final[tuple[tuple[PhraseCategory, re.Pattern[str]], ...]] = (
    (
        "investment_recommendation",
        re.compile(r"투자\s*(?:추천|권유)|(?:매수|매도)\s*(?:추천|권유)", re.IGNORECASE),
    ),
    (
        "investment_recommendation",
        re.compile(
            r"\b(?:investment|stock)\s+(?:recommendation|advice)\b"
            r"|\b(?:buy|sell)\s+(?:recommendation|tip)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "trading_signal",
        re.compile(r"(?:매수|매도|매매)\s*신호|(?:매수|매도)\s*타점", re.IGNORECASE),
    ),
    (
        "trading_signal",
        re.compile(r"\b(?:buy|sell|trading|entry|exit)\s+signal\b", re.IGNORECASE),
    ),
    (
        "return_guarantee",
        re.compile(
            r"(?:수익|수익률|원금)\s*(?:을|이)?\s*보장"
            r"|무손실|확정\s*수익|고수익\s*보장",
            re.IGNORECASE,
        ),
    ),
    (
        "return_guarantee",
        re.compile(
            r"\bguaranteed?\s+(?:return|returns|profit|profits|income)\b"
            r"|\b(?:return|returns|profit|profits|income)\s+guaranteed\b"
            r"|\b(?:return|returns|profit|income)\s+guarantee\b"
            r"|\b(?:risk[- ]free|no[- ]loss|principal\s+guaranteed)\b",
            re.IGNORECASE,
        ),
    ),
)


def _validate_text(text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("publication text must be a string")


def find_banned_phrases(text: str) -> tuple[BannedPhraseMatch, ...]:
    """Return all prohibited phrase matches, ordered by location."""

    _validate_text(text)
    matches: list[BannedPhraseMatch] = []
    for category, pattern in _PATTERNS:
        for found in pattern.finditer(text):
            matches.append(
                BannedPhraseMatch(
                    category=category,
                    phrase=found.group(0),
                    start=found.start(),
                    end=found.end(),
                )
            )
    matches.sort(key=lambda match: (match.start, match.end, match.category))
    return tuple(matches)


def lint_text(text: str) -> str:
    """Return text when clean; raise :class:`BannedPhraseError` otherwise."""

    matches = find_banned_phrases(text)
    if matches:
        raise BannedPhraseError(matches[0])
    return text


def assert_no_banned_phrases(text: str) -> str:
    """Explicit alias for the publication lint used by callers and tests."""

    return lint_text(text)


__all__ = [
    "BANNED_PHRASES",
    "BannedPhraseError",
    "BannedPhraseMatch",
    "assert_no_banned_phrases",
    "find_banned_phrases",
    "lint_text",
]
