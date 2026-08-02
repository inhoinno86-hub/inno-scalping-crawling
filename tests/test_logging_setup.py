from __future__ import annotations

import json
import logging
from io import StringIO

from scalping_briefing.logging_setup import configure_logging, mask_secrets


def test_structured_log_masks_named_and_suffix_secrets() -> None:
    stream = StringIO()
    logger = logging.getLogger("test.secret-mask")
    configure_logging(stream=stream, logger=logger)

    logger.info(
        "credentials REVIEW_API_TOKEN=review-value",
        extra={
            "REVIEW_API_TOKEN": "review-value",
            "TELEGRAM_BOT_TOKEN": "telegram-value",
            "CUSTOM_KEY": "key-value",
            "nested": {"SERVICE_SECRET": "secret-value", "safe": "visible"},
        },
    )

    record = json.loads(stream.getvalue())
    serialized = stream.getvalue()
    for secret in ("review-value", "telegram-value", "key-value", "secret-value"):
        assert secret not in serialized
    assert record["REVIEW_API_TOKEN"] == "[REDACTED]"
    assert record["nested"]["safe"] == "visible"


def test_environment_secret_is_masked_even_inside_message(monkeypatch) -> None:
    secret = "env-review-secret"
    monkeypatch.setenv("REVIEW_API_TOKEN", secret)
    stream = StringIO()
    logger = logging.getLogger("test.secret-mask.env")
    configure_logging(stream=stream, logger=logger)

    logger.info("received %s", secret)

    assert secret not in stream.getvalue()


def test_recursive_masking_preserves_non_secret_fields() -> None:
    value = mask_secrets(
        {
            "TELEGRAM_BOT_TOKEN": "abc",
            "items": [{"PUBLIC": "ok", "DB_PASSWORD": "not-suffix-secret"}],
        }
    )

    assert value["TELEGRAM_BOT_TOKEN"] == "[REDACTED]"
    assert value["items"][0]["PUBLIC"] == "ok"
    assert value["items"][0]["DB_PASSWORD"] == "not-suffix-secret"
