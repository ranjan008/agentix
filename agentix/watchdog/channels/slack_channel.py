"""
Slack channel adapter — uses Slack Bolt async (Socket Mode or Events API).
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Awaitable

from agentix.watchdog import trigger_normalizer as tn

logger = logging.getLogger(__name__)


class SlackChannel:
    """
    Wraps slack_bolt AsyncApp.
    Supports Socket Mode (app-level token) or Events API (HTTP).

    Socket Mode requires:  SLACK_APP_TOKEN (xapp-...) + SLACK_BOT_TOKEN (xoxb-...)
    Events API requires:   SLACK_BOT_TOKEN + SLACK_SIGNING_SECRET
    """

    def __init__(
        self,
        app_token: str = "",
        bot_token: str = "",
        signing_secret: str = "",
        default_agent_id: str = "",
        on_trigger: Callable[[dict], Awaitable[None]] | None = None,
        agent_router: Callable[[str, str], str] | None = None,
    ) -> None:
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self.signing_secret = signing_secret or os.environ.get("SLACK_SIGNING_SECRET", "")
        self.default_agent_id = default_agent_id
        self.on_trigger = on_trigger
        # Optional: map (channel_id, text) -> agent_id
        self.agent_router = agent_router
        self._handler = None

    def _get_agent_id(self, channel_id: str, text: str) -> str:
        if self.agent_router:
            return self.agent_router(channel_id, text) or self.default_agent_id
        return self.default_agent_id

    async def start(self) -> None:
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            logger.warning("slack_bolt not installed — Slack channel disabled. Run: pip install slack-bolt")
            return

        app = AsyncApp(token=self.bot_token, signing_secret=self.signing_secret or None)

        @app.event("message")
        async def handle_message(event, say):
            # Ignore bot messages and subtypes (edits, etc.)
            if event.get("bot_id") or event.get("subtype"):
                return

            text = event.get("text", "")
            channel_id = event.get("channel", "")
            agent_id = self._get_agent_id(channel_id, text)

            if not agent_id:
                logger.debug("No agent mapped for Slack channel %s — ignoring", channel_id)
                return

            envelope = tn.from_slack(event, agent_id)
            logger.info("Slack trigger: agent=%s trigger=%s", agent_id, envelope["id"])

            if self.on_trigger:
                await self.on_trigger(envelope)

        @app.event("app_mention")
        async def handle_mention(event, say):
            text = event.get("text", "")
            channel_id = event.get("channel", "")
            agent_id = self._get_agent_id(channel_id, text)
            if not agent_id:
                return
            envelope = tn.from_slack(event, agent_id)
            logger.info("Slack mention trigger: agent=%s trigger=%s", agent_id, envelope["id"])
            if self.on_trigger:
                await self.on_trigger(envelope)

        if self.app_token:
            # Socket Mode — no inbound port needed
            self._handler = AsyncSocketModeHandler(app, self.app_token)
            await self._handler.start_async()
            logger.info("Slack channel started in Socket Mode")
        else:
            logger.warning(
                "SLACK_APP_TOKEN not set — Socket Mode disabled. "
                "Configure Events API separately to receive Slack events."
            )

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
