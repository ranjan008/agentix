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

from agentix.watchdog.agent_spawner import AgentSpawner
from agentix.watchdog.auth import RateLimiter
from agentix.watchdog.channels.registry import ChannelRegistry
from agentix.watchdog.config import load_config
from agentix.watchdog.rbac_gateway import RBACGateway
from agentix.watchdog.trigger_normalizer import TriggerEnvelope
from agentix.security.rbac import RBACEngine
from agentix.security.audit import AuditLog
from agentix.security.secrets import SecretsVault
from agentix.storage.standard import build_store
from agentix.scheduler.engine import Scheduler
from agentix.scheduler.loader import load_schedules_dir
from agentix.observability.cost_ledger import CostLedger
from agentix.observability.tracing import record_trigger
from agentix.orchestration.patterns import EventBus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("agentix.watchdog")


class Watchdog:
    def __init__(self, config_path: str = "config/watchdog.yaml") -> None:
        self.cfg = load_config(config_path)
        self.db_path = self.cfg.get("db_path", "data/agentix.db")

        # Storage (Lite: SQLite, Standard: PostgreSQL)
        self.store = build_store(self.cfg)

        # Audit log (HMAC-chained)
        audit_secret = os.environ.get("AUDIT_HMAC_SECRET", "")
        self.audit = AuditLog(db_path=self.db_path, hmac_secret=audit_secret)

        # Secrets vault
        secrets_cfg = self.cfg.get("secrets", {"backend": "env"})
        self.vault = SecretsVault.from_config(secrets_cfg)

        # RBAC engine
        security = self.cfg.get("security", {})
        policy_path = security.get("policy_file", "config/policy.yaml")
        if security.get("enforce_rbac", False):
            self.rbac = RBACEngine.from_yaml(policy_path)
        else:
            self.rbac = RBACEngine.permissive()

        self.rbac_gateway = RBACGateway(self.rbac, self.audit)

        # Cost ledger
        self.cost_ledger = CostLedger(db_path=self.db_path)

        # Scheduler (cron + one-shot + DAG)
        self.scheduler = Scheduler(
            db_path=self.db_path,
            tick_sec=self.cfg.get("scheduler", {}).get("tick_sec", 5.0),
            on_trigger=self._handle_trigger,
        )

        # Event bus (in-process agent-to-agent cascade)
        self.event_bus = EventBus(on_trigger=self._handle_trigger)
        for sub in self.cfg.get("event_subscriptions", []):
            self.event_bus.subscribe(sub["event"], sub["agent"])

        self.rate_limiter = RateLimiter(
            max_requests=self.cfg.get("rate_limit", {}).get("max_requests", 60),
            window_sec=self.cfg.get("rate_limit", {}).get("window_sec", 60),
        )
        self.spawner = AgentSpawner(
            max_concurrent=self.cfg.get("max_concurrent_agents", 10),
            spawn_timeout_sec=self.cfg.get("agent_timeout_sec", self.cfg.get("shutdown_timeout_sec", 600)),
            db_path=self.db_path,
            on_complete=self._on_agent_complete,
        )
        self._channel_registry: ChannelRegistry | None = None
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Trigger pipeline
    # ------------------------------------------------------------------

    async def _handle_trigger(self, envelope) -> None:
        """Validate agent exists, persist trigger, spawn agent."""
        # Accept both TriggerEnvelope dataclass and plain dicts
        if isinstance(envelope, TriggerEnvelope):
            envelope = envelope.to_dict()
        agent_id = envelope["agent_id"]
        trigger_id = envelope["id"]

        # Verify agent is registered
        agent_spec = self.store.get_agent(agent_id)
        if not agent_spec:
            logger.warning("Unknown agent '%s' — trigger %s rejected", agent_id, trigger_id)
            self.audit.record(
                "trigger.rejected", trigger_id, agent_id,
                detail={"reason": "unknown_agent"},
                tenant_id=envelope["caller"].get("tenant_id", "default"),
            )
            return

        # RBAC gateway check
        if not self.rbac_gateway.check_trigger(envelope, agent_spec):
            return

        # OTel root span
        with record_trigger(envelope):
            pass  # span context propagated via OTel context vars to child processes

        # Persist & audit
        self.store.create_trigger(envelope)
        self.audit.record(
            "trigger.received", trigger_id, agent_id,
            envelope["caller"]["identity_id"],
            tenant_id=envelope["caller"].get("tenant_id", "default"),
        )

        # Spawn
        self.store.update_trigger_status(trigger_id, "running")
        await self.spawner.spawn(envelope)

    def _on_agent_complete(self, trigger_id: str, success: bool, error: str | None) -> None:
        status = "done" if success else "failed"
        self.store.update_trigger_status(trigger_id, status, error)
        self.audit.record(
            f"agent.{status}",
            trigger_id=trigger_id,
            detail={"error": error} if error else {},
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        logger.info("Agentix Watchdog starting (tier=%s)", self.cfg.get("infra_tier", "lite"))

        # Build aiohttp app for channels that need HTTP routes
        from aiohttp import web
        app = web.Application()

        self._channel_registry = ChannelRegistry(self.cfg, self._handle_trigger, app)
        await self._channel_registry.start_all()

        # Load schedule YAML files
        schedules_dir = self.cfg.get("scheduler", {}).get("schedules_dir", "schedules")
        loaded = load_schedules_dir(self.scheduler, schedules_dir)
        if loaded:
            logger.info("Scheduler: loaded %d schedule(s) from %s", loaded, schedules_dir)

        # Start scheduler as background task
        scheduler_task = asyncio.create_task(self.scheduler.run(), name="agentix-scheduler")

        logger.info("Watchdog ready — all configured channels active")

        # Wait for shutdown signal
        loop = asyncio.get_running_loop()
        try:
            # Unix only
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._stop_event.set)
        except NotImplementedError:
            # Windows fallback — signal.signal() is synchronous; use
            # call_soon_threadsafe to safely set the asyncio Event.
            def _win_handler(signum, frame):
                loop.call_soon_threadsafe(self._stop_event.set)
            signal.signal(signal.SIGINT, _win_handler)
            try:
                signal.signal(signal.SIGTERM, _win_handler)
            except (OSError, ValueError):
                pass  # SIGTERM may not be available on Windows

        await self._stop_event.wait()
        scheduler_task.cancel()
        await self.stop()

    async def stop(self) -> None:
        logger.info("Watchdog shutting down…")
        await self.scheduler.stop()
        if self._channel_registry:
            await self._channel_registry.stop_all()
        logger.info("Watchdog stopped. Active agents at shutdown: %d", self.spawner.active_count)


def run(config_path: str = "config/watchdog.yaml") -> None:
    """Entry point called by the CLI."""
    watchdog = Watchdog(config_path)
    asyncio.run(watchdog.start())


if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config/watchdog.yaml"
    run(cfg)
