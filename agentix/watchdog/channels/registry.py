"""
Channel registry — discovers and starts all configured inbound channels.

Usage (in Watchdog.start):

    from agentix.watchdog.channels.registry import ChannelRegistry

    registry = ChannelRegistry(cfg, on_trigger=self._dispatch_trigger, app=self._app)
    await registry.start_all()
    ...
    await registry.stop_all()

Each channel is enabled when its primary credential/config key is present
OR when explicitly enabled in config: channels.<name>.enabled = true.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)


class ChannelRegistry:
    """Manages lifecycle of all inbound channel adapters."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._cfg = cfg
        self._on_trigger: Any = on_trigger  # channels type as Callable[[dict], ...]; cast to Any
        self._app = app
        self._channels: list[Any] = []

    def _channel_cfg(self, name: str) -> dict:
        """Merge top-level cfg with channels.<name> sub-dict."""
        base = dict(self._cfg)
        base.update(self._cfg.get("channels", {}).get(name, {}))
        return base

    def _enabled(self, name: str, default_keys: list[str]) -> bool:
        ch_cfg = self._channel_cfg(name)
        explicit = ch_cfg.get("enabled")
        if explicit is not None:
            return bool(explicit)
        import os
        return any(ch_cfg.get(k) or os.environ.get(k.upper()) for k in default_keys)

    # ------------------------------------------------------------------
    # Channel factories
    # ------------------------------------------------------------------

    def _build_http(self) -> Any:
        from agentix.watchdog.channels.http_webhook import HttpWebhookChannel
        cfg = self._channel_cfg("http")
        return HttpWebhookChannel(
            port=int(cfg.get("http_port", 8080)),
            path=cfg.get("http_path", "/trigger"),
            jwt_secret=cfg.get("jwt_secret", ""),
            on_trigger=self._on_trigger,
        )

    def _build_slack(self) -> Any:
        if not self._enabled("slack", ["slack_bot_token", "slack_signing_secret"]):
            return None
        from agentix.watchdog.channels.slack_channel import SlackChannel
        cfg = self._channel_cfg("slack")
        return SlackChannel(
            app_token=cfg.get("slack_app_token", ""),
            bot_token=cfg.get("slack_bot_token", ""),
            signing_secret=cfg.get("slack_signing_secret", ""),
            default_agent_id=cfg.get("default_agent_id", ""),
            on_trigger=self._on_trigger,
        )

    def _build_telegram(self) -> Any:
        if not self._enabled("telegram", ["telegram_bot_token"]):
            return None
        from agentix.watchdog.channels.telegram import TelegramChannel
        return TelegramChannel(self._channel_cfg("telegram"), self._on_trigger, self._app)

    def _build_whatsapp(self) -> Any:
        if not self._enabled("whatsapp", ["whatsapp_access_token"]):
            return None
        from agentix.watchdog.channels.whatsapp import WhatsAppChannel
        return WhatsAppChannel(self._channel_cfg("whatsapp"), self._on_trigger, self._app)

    def _build_teams(self) -> Any:
        if not self._enabled("teams", ["teams_app_id", "teams_app_password"]):
            return None
        from agentix.watchdog.channels.teams import TeamsChannel
        return TeamsChannel(self._channel_cfg("teams"), self._on_trigger, self._app)

    def _build_email(self) -> Any:
        if not self._enabled("email", ["email_imap_host"]):
            return None
        from agentix.watchdog.channels.email_channel import EmailChannel
        return EmailChannel(self._channel_cfg("email"), self._on_trigger, self._app)

    def _build_sqs(self) -> Any:
        if not self._enabled("sqs", ["sqs_queue_url"]):
            return None
        from agentix.watchdog.channels.sqs import SQSChannel
        return SQSChannel(self._channel_cfg("sqs"), self._on_trigger, self._app)

    def _build_grpc(self) -> Any:
        if not self._enabled("grpc", ["grpc_listen_port"]):
            return None
        from agentix.watchdog.channels.grpc_channel import GRPCChannel
        return GRPCChannel(self._channel_cfg("grpc"), self._on_trigger, self._app)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_all(self) -> None:
        builders = [
            self._build_http,
            self._build_slack,
            self._build_telegram,
            self._build_whatsapp,
            self._build_teams,
            self._build_email,
            self._build_sqs,
            self._build_grpc,
        ]
        for build in builders:
            try:
                ch = build()
                if ch is None:
                    continue
                await ch.start()
                self._channels.append(ch)
                log.info("Channel %s started", ch.__class__.__name__)
            except Exception as exc:
                log.error("Failed to start channel %s: %s", build.__name__, exc, exc_info=True)

    async def stop_all(self) -> None:
        for ch in reversed(self._channels):
            try:
                await ch.stop()
            except Exception as exc:
                log.warning("Error stopping channel %s: %s", ch.__class__.__name__, exc)
        self._channels.clear()
