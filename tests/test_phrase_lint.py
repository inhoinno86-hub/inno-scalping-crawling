from __future__ import annotations

import pytest

from scalping_briefing.publishing.phrase_lint import (
    BannedPhraseError,
    assert_no_banned_phrases,
    find_banned_phrases,
    lint_text,
)


@pytest.mark.parametrize(
    "phrase",
    (
        "매수 추천 의견",
        "매매 신호가 발생했다",
        "수익 보장 상품",
        "This is a buy signal.",
        "The strategy offers guaranteed return.",
    ),
)
def test_banned_korean_and_english_phrases_are_rejected(phrase: str) -> None:
    with pytest.raises(BannedPhraseError):
        lint_text(phrase)


def test_lint_reproduces_failure_with_match_details() -> None:
    with pytest.raises(BannedPhraseError) as caught:
        assert_no_banned_phrases("이 문서는 투자 권유가 아니다")

    assert caught.value.match.category == "investment_recommendation"
    assert caught.value.match.phrase == "투자 권유"


def test_clean_research_language_passes() -> None:
    text = "The note compares queue imbalance observations and execution latency."
    assert lint_text(text) == text
    assert find_banned_phrases(text) == ()
