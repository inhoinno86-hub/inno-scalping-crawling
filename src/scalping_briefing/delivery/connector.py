"""Offline delivery connector contracts and Telegram dry-run behavior.

The Phase 3 delivery boundary is deliberately provider-free.  A connector can
render a briefing and record a dry-run attempt, but this module has no live
transport implementation and does not import a network client.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import logging
import os
from os import PathLike
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from scalping_briefing.logging_setup import log_event, mask_secrets
from scalping_briefing.publishing.briefing_render import render_briefing_markdown
from scalping_briefing.storage.files import LocalFileStorage


DEFAULT_CHANNEL = "telegram"
DEFAULT_STORAGE_ROOT = Path("storage")
_DEFAULT_RAW_RETENTION_DAYS = 365
_DEFAULT_NORMALIZED_RETENTION_DAYS = "unlimited"


class DeliveryConnectorError(RuntimeError):
    """Base error for connector failures."""


class LiveDeliveryRejected(DeliveryConnectorError):
    """Raised because this phase has no live-delivery implementation."""


DeliveryModeError = LiveDeliveryRejected
LiveDeliveryNotSupported = LiveDeliveryRejected


@dataclass(frozen=True, slots=True)
class DeliveryAttemptResult:
    """Result of recording one connector attempt.

    ``message`` is intentionally not retained.  The rendered content is
    available through the local artifact path, while the result only carries
    its deterministic hash and delivery metadata.
    """

    channel: str
    content_hash: str
    # A successfully written local artifact is a successful attempt.  The
    # separate ``dry_run`` flag keeps this compatible with the Delivery model's
    # status enum while making the non-provider behavior explicit.
    status: str = "success"
    artifact_path: Path | None = None
    attempted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    dry_run: bool = True
    provider_reference: str | None = None
    error: str | None = None

    @property
    def artifact(self) -> Path | None:
        """Compatibility alias for callers that refer to the artifact."""

        return self.artifact_path

    @property
    def artifact_location(self) -> str | None:
        """Return the artifact path as a serializable location, if present."""

        return str(self.artifact_path) if self.artifact_path is not None else None


@runtime_checkable
class DeliveryConnector(Protocol):
    """Contract shared by delivery providers."""

    channel: str

    def render(self, briefing_payload: Any) -> str:
        """Render a briefing payload into a provider message."""

    def send(self, message: str, *, dry_run: bool) -> DeliveryAttemptResult:
        """Record or send a message according to the connector policy."""


def _value(settings: Any, name: str, default: Any = None) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name, default)
    if settings is None:
        return default
    return getattr(settings, name, default)


def _storage_settings(settings: Any) -> dict[str, Any]:
    """Adapt existing retention settings to the fixed storage contract."""

    return {
        "raw_retention_days": _value(
            settings, "raw_retention_days", _DEFAULT_RAW_RETENTION_DAYS
        ),
        "normalized_retention_days": _value(
            settings,
            "normalized_retention_days",
            _DEFAULT_NORMALIZED_RETENTION_DAYS,
        ),
    }


class TelegramDryRunConnector:
    """Telegram-shaped connector that only writes local dry-run artifacts.

    Credentials are read from the process environment for masking purposes;
    they are never accepted as constructor/configuration values and never
    written to the artifact.  No network operation exists in this class.
    """

    channel = DEFAULT_CHANNEL

    def __init__(
        self,
        storage_root: str | PathLike[str] = DEFAULT_STORAGE_ROOT,
        *,
        settings: Any | None = None,
        storage: LocalFileStorage | None = None,
        artifact_root: str | PathLike[str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if artifact_root is not None:
            if Path(storage_root) != DEFAULT_STORAGE_ROOT:
                raise TypeError("use storage_root or artifact_root, not both")
            storage_root = artifact_root
        if storage is not None and artifact_root is not None:
            raise TypeError("use storage or artifact_root, not both")

        self.settings = settings
        self.storage = storage or LocalFileStorage(
            storage_root,
            settings=_storage_settings(settings),
        )
        self.logger = logger or logging.getLogger(__name__)

    def render(self, briefing_payload: Any) -> str:
        """Render a payload using the existing bounded briefing renderer."""

        if isinstance(briefing_payload, str):
            return briefing_payload
        return str(
            render_briefing_markdown(
                briefing_payload,
                settings=self.settings or {},
            )
        )

    def _delivery_mode(self) -> str:
        configured = _value(self.settings, "DELIVERY_MODE", None)
        environment = os.environ.get("DELIVERY_MODE")
        modes = [value for value in (configured, environment) if value is not None]
        if any(str(value).strip().lower() != "dry_run" for value in modes):
            return "live"
        return "dry_run"

    @staticmethod
    def _environment_secrets() -> set[str]:
        return {
            value
            for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
            if (value := os.environ.get(name))
        }

    def send(self, message: str, *, dry_run: bool) -> DeliveryAttemptResult:
        """Write a normalized local artifact and emit a masked event.

        ``dry_run`` is checked together with ``DELIVERY_MODE`` so either a
        caller flag or an environment/configuration request for live delivery
        fails closed before any artifact write.
        """

        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if not dry_run or self._delivery_mode() != "dry_run":
            raise LiveDeliveryRejected(
                "only DELIVERY_MODE=dry_run is supported; live delivery is disabled"
            )

        # Redact any accidentally embedded environment secret before it can
        # reach the fixed local storage namespace.  The hash remains derived
        # from the rendered message supplied by the caller, as required by
        # the delivery idempotency contract.
        safe_message = str(
            mask_secrets(message, secret_values=self._environment_secrets())
        )
        content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        artifact_id = f"telegram-{content_hash}"
        artifact_path = self.storage.write_normalized(artifact_id, safe_message)

        # Only non-sensitive metadata is logged.  ``log_event`` is the
        # repository's structured logging/masking boundary.
        log_event(
            self.logger,
            logging.INFO,
            "telegram_delivery_dry_run",
            channel=self.channel,
            status="success",
            delivery_mode="dry_run",
            content_hash=content_hash,
            artifact_path=str(artifact_path),
        )
        return DeliveryAttemptResult(
            channel=self.channel,
            content_hash=content_hash,
            status="success",
            artifact_path=artifact_path,
            dry_run=True,
        )


TELEGRAM_API_BASE = "https://api.telegram.org"
_TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_LIVE_SEND_TIMEOUT = 10.0


class TelegramSendError(DeliveryConnectorError):
    """Raised when a live Telegram Bot API send fails or is rejected."""


def _chunk_message(message: str, limit: int = _TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split on Telegram's hard per-message character limit, never truncate."""

    if not message:
        return [""]
    return [message[index : index + limit] for index in range(0, len(message), limit)]


class TelegramLiveConnector:
    """Telegram connector that performs a real Bot API ``sendMessage`` call.

    Credentials (``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``) are read from
    the process environment at send time only, never accepted as
    constructor/configuration values -- same rule as
    :class:`TelegramDryRunConnector`. When ``send`` is called with
    ``dry_run=True`` this delegates to an internal
    :class:`TelegramDryRunConnector` for identical local-artifact behavior
    (audit parity); a live network call only happens when ``dry_run=False``.
    """

    channel = DEFAULT_CHANNEL

    def __init__(
        self,
        storage_root: str | PathLike[str] = DEFAULT_STORAGE_ROOT,
        *,
        settings: Any | None = None,
        storage: LocalFileStorage | None = None,
        artifact_root: str | PathLike[str] | None = None,
        logger: logging.Logger | None = None,
        timeout: float = DEFAULT_LIVE_SEND_TIMEOUT,
    ) -> None:
        self._dry_run_connector = TelegramDryRunConnector(
            storage_root,
            settings=settings,
            storage=storage,
            artifact_root=artifact_root,
            logger=logger,
        )
        self.settings = settings
        self.storage = self._dry_run_connector.storage
        self.logger = logger or logging.getLogger(__name__)
        self.timeout = timeout

    def render(self, briefing_payload: Any) -> str:
        return self._dry_run_connector.render(briefing_payload)

    def send(self, message: str, *, dry_run: bool) -> DeliveryAttemptResult:
        if not isinstance(message, str):
            raise TypeError("message must be a string")
        if dry_run:
            return self._dry_run_connector.send(message, dry_run=True)

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not bot_token or not chat_id:
            raise TelegramSendError(
                "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set in "
                "the process environment for a live send"
            )

        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        last_message_id: Any = None
        for chunk in _chunk_message(message):
            response = httpx.post(
                url,
                json={"chat_id": chat_id, "text": chunk},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise TelegramSendError(f"Telegram API rejected the message: {payload}")
            last_message_id = payload.get("result", {}).get("message_id")

        content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        log_event(
            self.logger,
            logging.INFO,
            "telegram_delivery_live",
            channel=self.channel,
            status="success",
            delivery_mode="live",
            content_hash=content_hash,
        )
        return DeliveryAttemptResult(
            channel=self.channel,
            content_hash=content_hash,
            status="success",
            dry_run=False,
            provider_reference=str(last_message_id) if last_message_id is not None else None,
        )


__all__ = [
    "DEFAULT_CHANNEL",
    "DEFAULT_LIVE_SEND_TIMEOUT",
    "DEFAULT_STORAGE_ROOT",
    "DeliveryAttemptResult",
    "DeliveryConnector",
    "DeliveryConnectorError",
    "DeliveryModeError",
    "LiveDeliveryNotSupported",
    "LiveDeliveryRejected",
    "TELEGRAM_API_BASE",
    "TelegramDryRunConnector",
    "TelegramLiveConnector",
    "TelegramSendError",
]
