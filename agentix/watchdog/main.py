"""
Watchdog — the always-on, lightweight process.
Reads config, starts channel adapters, auth-checks triggers, spawns agents.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from agentix.watchdog.agent_spawner import AgentSpawner
from agentix.watchdog.auth import RateLimiter
from agentix.watchdog.channels.http_webhook import HttpWebhookChannel
from agentix.watchdog.channels.slack_channel import SlackChannel
from agentix.watchdog.config import load_config
from agentix.storage.state_store import StateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentix.watchdog")


class Watchdog:
    def __init__(self, config_path: str = "config/watchdog.yaml") -> None:
        self.cfg = load_config(config_path)
        self.db_path = self.cfg.get("db_path", "data/agentix.db")
        self.store = StateStore(self.db_path)
        self.rate_limiter = RateLimiter(
            max_requests=self.cfg.get("rate_limit", {}).get("max_requests", 60),
            window_sec=self.cfg.get("rate_limit", {}).get("window_sec", 60),
        )
        self.spawner = AgentSpawner(
            max_concurrent=self.cfg.get("max_concurrent_agents", 10),
            spawn_timeout_sec=self.cfg.get("shutdown_timeout_sec", 120),
            db_path=self.db_path,
            on_complete=self._on_agent_complete,
        )
        self._channels: list = []
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Trigger pipeline
    # ------------------------------------------------------------------

    async def _handle_trigger(self, envelope: dict) -> None:
        """Validate agent exists, persist trigger, spawn agent."""
        agent_id = envelope["agent_id"]
        trigger_id = envelope["id"]

        # Verify agent is registered
        agent_spec = self.store.get_agent(agent_id)
        if not agent_spec:
            logger.warning("Unknown agent '%s' — trigger %s rejected", agent_id, trigger_id)
            self.store.audit("trigger.rejected", trigger_id, agent_id, detail={"reason": "unknown_agent"})
            return

        # Persist & audit
        self.store.create_trigger(envelope)
        self.store.audit("trigger.received", trigger_id, agent_id, envelope["caller"]["identity_id"])

        # Spawn
        self.store.update_trigger_status(trigger_id, "running")
        await self.spawner.spawn(envelope)

    def _on_agent_complete(self, trigger_id: str, success: bool, error: str | None) -> None:
        status = "done" if success else "failed"
        self.store.update_trigger_status(trigger_id, status, error)
        self.store.audit(
            f"agent.{status}",
            trigger_id,
            detail={"error": error} if error else {},
        )

    # ------------------------------------------------------------------
    # Channel setup
    # ------------------------------------------------------------------

    def _build_channels(self) -> None:
        security = self.cfg.get("security", {})
        jwt_secret = os.environ.get(
            security.get("jwt_secret_env", "JWT_SECRET"), ""
        )
        enforce_rbac = security.get("enforce_rbac", False)

        for ch_cfg in self.cfg.get("channels", []):
            ch_type = ch_cfg.get("type", "")

            if ch_type == "http_webhook":
                channel = HttpWebhookChannel(
                    port=ch_cfg.get("port", 8080),
                    path=ch_cfg.get("path", "/trigger"),
                    jwt_secret=jwt_secret,
                    enforce_auth=enforce_rbac,
                    hmac_secret=ch_cfg.get("hmac_secret", ""),
                    on_trigger=self._handle_trigger,
                    rate_limiter=self.rate_limiter,
                )
                self._channels.append(channel)

            elif ch_type == "slack":
                channel = SlackChannel(
                    app_token=ch_cfg.get("app_token", ""),
                    bot_token=ch_cfg.get("bot_token", ""),
                    signing_secret=ch_cfg.get("signing_secret", ""),
                    default_agent_id=ch_cfg.get("default_agent_id", ""),
                    on_trigger=self._handle_trigger,
                )
                self._channels.append(channel)

            else:
                logger.warning("Unknown channel type '%s' — skipping", ch_type)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info("Agentix Watchdog starting (tier=%s)", self.cfg.get("infra_tier", "lite"))
        self._build_channels()

        for ch in self._channels:
            await ch.start()

        logger.info(
            "Watchdog ready — %d channel(s) active, max_concurrent_agents=%d",
            len(self._channels),
            self.cfg.get("max_concurrent_agents", 10),
        )

        # Wait for shutdown signal
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._stop_event.set)

        await self._stop_event.wait()
        await self.stop()

    async def stop(self) -> None:
        logger.info("Watchdog shutting down…")
        for ch in self._channels:
            await ch.stop()
        logger.info("Watchdog stopped. Active agents at shutdown: %d", self.spawner.active_count)


def run(config_path: str = "config/watchdog.yaml") -> None:
    """Entry point called by the CLI."""
    watchdog = Watchdog(config_path)
    asyncio.run(watchdog.start())


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config/watchdog.yaml"
    run(cfg)
