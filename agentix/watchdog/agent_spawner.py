"""
Agent Spawner — forks a child process to run the agent runtime.

Spawn modes (Phase 1): process fork only.
Future: container (docker run), lambda invoke.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Callable

logger = logging.getLogger(__name__)


class AgentSpawner:
    def __init__(
        self,
        max_concurrent: int = 10,
        spawn_timeout_sec: int = 120,
        db_path: str = "data/agentix.db",
        on_complete: Callable[[str, bool, str | None], None] | None = None,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.spawn_timeout_sec = spawn_timeout_sec
        self.db_path = db_path
        self.on_complete = on_complete
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active: dict[str, asyncio.Task] = {}

    async def spawn(self, envelope: dict) -> None:
        """
        Spawn an agent process for the given TriggerEnvelope.
        Returns immediately; agent runs in background.
        """
        trigger_id = envelope["id"]

        if len(self._active) >= self.max_concurrent:
            logger.warning("Max concurrent agents reached (%d), queuing %s", self.max_concurrent, trigger_id)

        task = asyncio.create_task(
            self._run_agent(envelope),
            name=f"agent-{trigger_id}",
        )
        self._active[trigger_id] = task
        task.add_done_callback(lambda t: self._active.pop(trigger_id, None))

    async def _run_agent(self, envelope: dict) -> None:
        trigger_id = envelope["id"]
        agent_id = envelope["agent_id"]

        async with self._semaphore:
            logger.info("Spawning agent process: agent=%s trigger=%s", agent_id, trigger_id)

            # Pass the envelope to the agent runtime via env variable
            env = {
                **os.environ,
                "AGENTIX_TRIGGER": json.dumps(envelope),
                "AGENTIX_DB_PATH": self.db_path,
            }

            # Locate the agent_runtime entry point
            runtime_module = "agentix.agent_runtime.main"

            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", runtime_module,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=self.spawn_timeout_sec,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    logger.error("Agent timed out: agent=%s trigger=%s", agent_id, trigger_id)
                    if self.on_complete:
                        self.on_complete(trigger_id, False, "timeout")
                    return

                if proc.returncode == 0:
                    out = stdout.decode(errors="replace").strip()
                    err = stderr.decode(errors="replace").strip()
                    logger.info("Agent completed: agent=%s trigger=%s", agent_id, trigger_id)
                    if err:
                        logger.debug("Agent stderr: agent=%s\n%s", agent_id, err[-2000:])
                    if self.on_complete:
                        self.on_complete(trigger_id, True, None)
                else:
                    err = stderr.decode(errors="replace").strip()
                    logger.error(
                        "Agent failed (rc=%d): agent=%s trigger=%s\n%s",
                        proc.returncode, agent_id, trigger_id, err,
                    )
                    if self.on_complete:
                        self.on_complete(trigger_id, False, err[-500:] if err else "non-zero exit")

            except Exception as exc:
                logger.exception("Spawn error: agent=%s trigger=%s: %s", agent_id, trigger_id, exc)
                if self.on_complete:
                    self.on_complete(trigger_id, False, str(exc))

    @property
    def active_count(self) -> int:
        return len(self._active)
