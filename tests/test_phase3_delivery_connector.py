from __future__ import annotations

import hashlib
import json
from io import StringIO
import logging
from pathlib import Path
import socket

import pytest

from scalping_briefing.delivery.connector import (
    DeliveryConnector,
    LiveDeliveryRejected,
    TelegramDryRunConnector,
)
from scalping_briefing.logging_setup import configure_logging


def test_telegram_connector_provides_render_contract(tmp_path: Path) -> None:
    connector = TelegramDryRunConnector(storage_root=tmp_path / "storage")

    assert isinstance(connector, DeliveryConnector)
    rendered = connector.render(
        {
            "briefing_id": "briefing-1",
            "generated_at": "2026-08-03T08:00:00+09:00",
            "timezone": "Asia/Seoul",
            "window_start": "2026-07-20T08:00:00+09:00",
            "window_end": "2026-08-03T08:00:00+09:00",
            "publication_status": "draft",
            "items": [],
        }
    )

    assert isinstance(rendered, str)
    assert "briefing-1" in rendered


def test_dry_run_writes_normalized_artifact_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open a socket")

    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(socket, "create_connection", blocked_socket)
    connector = TelegramDryRunConnector(storage_root=tmp_path / "storage")
    message = "# briefing\nlocal dry-run"

    result = connector.send(message, dry_run=True)

    assert result.status == "success"
    assert result.channel == "telegram"
    assert result.dry_run is True
    assert result.artifact_path == (
        tmp_path / "storage" / "normalized" / f"telegram-{result.content_hash}"
    )
    assert result.artifact_path.read_text(encoding="utf-8") == message


def test_live_mode_is_rejected_before_writing(tmp_path: Path) -> None:
    connector = TelegramDryRunConnector(
        storage_root=tmp_path / "storage",
        settings={"DELIVERY_MODE": "live"},
    )

    with pytest.raises(LiveDeliveryRejected, match="dry_run"):
        connector.send("must not be sent", dry_run=True)

    assert list((tmp_path / "storage" / "normalized").iterdir()) == []


def test_content_hash_is_sha256_and_deterministic(tmp_path: Path) -> None:
    message = "same rendered message"
    first = TelegramDryRunConnector(storage_root=tmp_path / "one").send(
        message, dry_run=True
    )
    second = TelegramDryRunConnector(storage_root=tmp_path / "two").send(
        message, dry_run=True
    )

    expected = hashlib.sha256(message.encode("utf-8")).hexdigest()
    assert first.content_hash == expected
    assert second.content_hash == expected


def test_environment_secrets_are_not_in_logs_or_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = "telegram-token-only-in-environment"
    chat_id = "chat-id-only-in-environment"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat_id)
    stream = StringIO()
    logger = logging.getLogger("test.phase3.delivery.connector.secrets")
    configure_logging(
        stream=stream,
        logger=logger,
        secrets=(token, chat_id),
    )
    connector = TelegramDryRunConnector(
        storage_root=tmp_path / "storage",
        logger=logger,
    )

    result = connector.send(f"public text {token} {chat_id}", dry_run=True)

    artifact = result.artifact_path.read_text(encoding="utf-8")
    logs = stream.getvalue()
    assert token not in artifact
    assert chat_id not in artifact
    assert token not in logs
    assert chat_id not in logs
    assert json.loads(logs)["status"] == "success"
    assert json.loads(logs)["delivery_mode"] == "dry_run"


def test_non_dry_run_flag_is_rejected_even_with_default_mode(
    tmp_path: Path,
) -> None:
    connector = TelegramDryRunConnector(storage_root=tmp_path / "storage")

    with pytest.raises(LiveDeliveryRejected):
        connector.send("must not be sent", dry_run=False)
