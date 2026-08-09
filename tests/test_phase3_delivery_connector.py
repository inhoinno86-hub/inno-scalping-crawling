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
    TelegramLiveConnector,
    TelegramSendError,
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


def test_live_connector_dry_run_delegates_without_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def blocked_post(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not call the Telegram API")

    monkeypatch.setattr("scalping_briefing.delivery.connector.httpx.post", blocked_post)
    connector = TelegramLiveConnector(storage_root=tmp_path / "storage")

    result = connector.send("local dry-run via live connector", dry_run=True)

    assert result.status == "success"
    assert result.dry_run is True
    assert result.artifact_path is not None


def test_live_connector_sends_one_real_message_when_not_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "result": {"message_id": 987}}

    def fake_post(url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("scalping_briefing.delivery.connector.httpx.post", fake_post)
    connector = TelegramLiveConnector(storage_root=tmp_path / "storage")

    result = connector.send("real message text", dry_run=False)

    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottest-token/sendMessage"
    assert calls[0]["json"] == {"chat_id": "12345", "text": "real message text"}
    assert result.status == "success"
    assert result.dry_run is False
    assert result.provider_reference == "987"


def test_live_connector_raises_without_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    def blocked_post(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not call the API without credentials")

    monkeypatch.setattr("scalping_briefing.delivery.connector.httpx.post", blocked_post)
    connector = TelegramLiveConnector(storage_root=tmp_path / "storage")

    with pytest.raises(TelegramSendError, match="TELEGRAM_BOT_TOKEN"):
        connector.send("must not be sent", dry_run=False)


def test_live_connector_raises_on_api_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    class RejectedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": False, "description": "chat not found"}

    monkeypatch.setattr(
        "scalping_briefing.delivery.connector.httpx.post",
        lambda *args, **kwargs: RejectedResponse(),
    )
    connector = TelegramLiveConnector(storage_root=tmp_path / "storage")

    with pytest.raises(TelegramSendError, match="chat not found"):
        connector.send("must not be sent", dry_run=False)


def test_live_connector_chunks_long_messages_without_truncating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    sent_chunks: list[str] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"ok": True, "result": {"message_id": len(sent_chunks)}}

    def fake_post(url: str, *, json: dict[str, object], timeout: float) -> FakeResponse:
        sent_chunks.append(json["text"])
        return FakeResponse()

    monkeypatch.setattr("scalping_briefing.delivery.connector.httpx.post", fake_post)
    connector = TelegramLiveConnector(storage_root=tmp_path / "storage")
    long_message = "x" * 9000

    connector.send(long_message, dry_run=False)

    assert len(sent_chunks) == 3
    assert "".join(sent_chunks) == long_message
    assert all(len(chunk) <= 4096 for chunk in sent_chunks)
