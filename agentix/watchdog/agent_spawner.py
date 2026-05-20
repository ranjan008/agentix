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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

# Resolve project root from this file's location (agentix/watchdog/agent_spawner.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])

_thread_pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix="agent-spawn")

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

            env = dict(os.environ)
            try:
                from dotenv import dotenv_values
                for k, v in dotenv_values().items():
                    if v is not None:
                        env.setdefault(k, v)
            except Exception:
                pass
            env["AGENTIX_TRIGGER"] = json.dumps(envelope)
            env["AGENTIX_DB_PATH"] = self.db_path

            runtime_module = "agentix.agent_runtime.main"

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    _thread_pool,
                    self._run_subprocess,
                    agent_id, trigger_id, runtime_module, env,
                )
                success, err = result
                if success:
                    logger.info("Agent completed: agent=%s trigger=%s", agent_id, trigger_id)
                    if self.on_complete:
                        self.on_complete(trigger_id, True, None)
                else:
                    logger.error("Agent failed: agent=%s trigger=%s\n%s", agent_id, trigger_id, err)
                    if self.on_complete:
                        self.on_complete(trigger_id, False, err[-500:] if err else "non-zero exit")

            except Exception as exc:
                logger.exception("Spawn error: agent=%s trigger=%s: %s", agent_id, trigger_id, exc)
                if self.on_complete:
                    self.on_complete(trigger_id, False, str(exc))

    def _run_subprocess(
        self,
        agent_id: str,
        trigger_id: str,
        runtime_module: str,
        env: dict,
    ) -> tuple[bool, str]:
        """Blocking subprocess call — runs in thread pool to avoid Windows asyncio limitation."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", runtime_module],
                env=env,
                capture_output=True,
                timeout=self.spawn_timeout_sec,
                cwd=_PROJECT_ROOT,
            )
            stderr = result.stderr.decode(errors="replace").strip()
            if result.returncode == 0:
                if stderr:
                    logger.info("Agent stderr: agent=%s\n%s", agent_id, stderr[-3000:])
                return True, ""
            else:
                logger.error(
                    "Agent failed (rc=%d): agent=%s trigger=%s\n%s",
                    result.returncode, agent_id, trigger_id, stderr,
                )
                return False, stderr
        except subprocess.TimeoutExpired:
            logger.error("Agent timed out: agent=%s trigger=%s", agent_id, trigger_id)
            return False, "timeout"

    @property
    def active_count(self) -> int:
        return len(self._active)
