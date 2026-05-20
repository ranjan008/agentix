"""
Agent Swarm — coordinator pattern where a lead agent dynamically
delegates subtasks to specialist agents using the transfer_to_agent tool.

The swarm differs from ParallelFanOut:
  - FanOut fires ALL agents in parallel with the same prompt
  - Swarm lets the coordinator DECIDE which agents to call (LLM-driven routing)

Architecture:
  1. Coordinator agent receives the task with a roster of specialists
  2. Coordinator calls transfer_to_agent(agent_id, task) for each delegation
  3. ToolExecutor calls transfer_to_agent, which spawns + polls the specialist
  4. Coordinator synthesises all specialist responses into a final answer

This module provides:
  SwarmConfig — configuration for a swarm (coordinator + specialists)
  SwarmRunner — fires the swarm and waits for the coordinator to finish
  register_swarm_tools — injects swarm tools into a ToolExecutor
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class SwarmConfig:
    """
    Declares a coordinator agent and its available specialists.

    coordinator:   agent_id of the lead/planner agent
    specialists:   list of agent_ids the coordinator may call via transfer_to_agent
    task_timeout:  seconds to wait for individual specialists
    total_timeout: seconds to wait for the whole swarm to finish
    """

    def __init__(
        self,
        coordinator: str,
        specialists: list[str],
        task_timeout: float = 120.0,
        total_timeout: float = 600.0,
        description: str = "",
    ) -> None:
        self.coordinator = coordinator
        self.specialists = list(specialists)
        self.task_timeout = task_timeout
        self.total_timeout = total_timeout
        self.description = description

    def to_dict(self) -> dict:
        return {
            "coordinator": self.coordinator,
            "specialists": self.specialists,
            "task_timeout": self.task_timeout,
            "total_timeout": self.total_timeout,
            "description": self.description,
        }

    def specialist_roster_prompt(self) -> str:
        """Return a formatted list of specialists for inclusion in the system prompt."""
        lines = ["Available specialist agents you can delegate to:"]
        for s in self.specialists:
            lines.append(f"  - {s}")
        lines.append(
            "\nUse the 'transfer_to_agent' tool to delegate tasks. "
            "After receiving responses, synthesise them into a final answer."
        )
        return "\n".join(lines)


class SwarmRunner:
    """
    Fires a coordinator agent augmented with swarm tools and waits for completion.

    The coordinator's system prompt is automatically extended with the specialist
    roster so it knows which agents are available.
    """

    def __init__(
        self,
        config: SwarmConfig,
        db_path: str | Path = "data/agentix.db",
        on_trigger: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config
        self.db_path = Path(db_path)
        self.on_trigger = on_trigger

    async def run(
        self,
        task: str,
        caller: dict | None = None,
        context: dict | None = None,
    ) -> dict:
        """
        Dispatch task to the coordinator agent and wait for completion.
        Returns a result dict with status, coordinator_trigger_id, and response.
        """
        caller = caller or {
            "identity_id": "swarm-service",
            "roles": ["operator"],
            "tenant_id": "default",
        }

        # Build coordinator envelope
        from agentix.watchdog.trigger_normalizer import from_http

        swarm_id = f"swarm_{uuid.uuid4().hex[:10]}"
        full_task = (
            f"{task}\n\n"
            f"[Swarm context: You are the coordinator for swarm {swarm_id}. "
            f"Specialists: {', '.join(self.config.specialists)}]"
        )

        trigger_id = f"trig_{uuid.uuid4().hex[:16]}"
        envelope = from_http(
            body={
                "text": full_task,
                "agent_id": self.config.coordinator,
                "context": {
                    **(context or {}),
                    "swarm_id": swarm_id,
                    "swarm_specialists": self.config.specialists,
                },
            },
            headers={
                "x-identity-id": caller.get("identity_id", "swarm-service"),
                "x-roles": ",".join(caller.get("roles", ["operator"])),
                "x-tenant-id": caller.get("tenant_id", "default"),
            },
            agent_id=self.config.coordinator,
        )
        envelope["id"] = trigger_id

        logger.info(
            "Swarm %s starting: coordinator=%s specialists=%s",
            swarm_id, self.config.coordinator, self.config.specialists,
        )

        # Dispatch coordinator
        if self.on_trigger:
            await self.on_trigger(envelope)
        else:
            from agentix.watchdog.agent_spawner import AgentSpawner
            spawner = AgentSpawner(db_path=str(self.db_path))
            await spawner.spawn(envelope)

        # Wait for coordinator to complete
        deadline = time.time() + self.config.total_timeout
        while time.time() < deadline:
            result = self._poll_trigger(trigger_id)
            if result:
                logger.info("Swarm %s completed: status=%s", swarm_id, result.get("status"))
                return {
                    "swarm_id": swarm_id,
                    "coordinator": self.config.coordinator,
                    "trigger_id": trigger_id,
                    "status": result.get("status", "done"),
                    "response": result.get("response", ""),
                }
            await asyncio.sleep(2.0)

        return {
            "swarm_id": swarm_id,
            "coordinator": self.config.coordinator,
            "trigger_id": trigger_id,
            "status": "timeout",
            "response": f"Coordinator timed out after {self.config.total_timeout:.0f}s",
        }

    def _poll_trigger(self, trigger_id: str) -> dict | None:
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, response FROM triggers WHERE id=?", (trigger_id,)
            ).fetchone()
            conn.close()
            if row and row["status"] in ("done", "failed"):
                return dict(row)
        except Exception as exc:
            logger.warning("SwarmRunner poll error: %s", exc)
        return None
