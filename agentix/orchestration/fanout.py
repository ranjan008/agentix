"""
Parallel Fan-Out — dispatch one task to N agents simultaneously.

Backed by SQLite (fanout_runs + fanout_slots tables).
Suitable for API calls (fire-and-poll) and background batch workloads.

Usage:
    runner = FanoutRunner(db_path, on_trigger=watchdog.on_trigger)
    run_id = await runner.start(
        task="Analyse Q3 revenue",
        agents=["analyst-a", "analyst-b", "analyst-c"],
        context={"quarter": "Q3"},
        caller={"identity_id": "admin", "roles": ["operator"], "tenant_id": "default"},
    )
    result = await runner.wait(run_id, timeout_sec=120)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Awaitable

from agentix.watchdog.trigger_normalizer import from_http

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fanout_runs (
    id          TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    agents      TEXT NOT NULL,   -- JSON list of agent IDs
    context     TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
    started_at  REAL NOT NULL,
    finished_at REAL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    caller      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fanout_slots (
    id          TEXT PRIMARY KEY,    -- slot_{uuid}
    run_id      TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    trigger_id  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
    response    TEXT,
    FOREIGN KEY (run_id) REFERENCES fanout_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_fanout_slots_run ON fanout_slots(run_id);
"""


class FanoutRunner:
    """
    Creates and tracks parallel fan-out runs.

    `on_trigger` is the watchdog's _handle_trigger coroutine (or any callable
    that accepts a TriggerEnvelope dict).
    """

    def __init__(
        self,
        db_path: str | Path = "data/agentix.db",
        on_trigger: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.on_trigger = on_trigger
        self._init_db()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _tx(self):
        conn = self._conn()
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        task: str,
        agents: list[str],
        caller: dict,
        context: dict | None = None,
        tenant_id: str = "default",
    ) -> str:
        """
        Dispatch `task` to all `agents` in parallel.
        Returns a run_id immediately (non-blocking).
        """
        if not agents:
            raise ValueError("agents list must not be empty")

        run_id = f"fanout_{uuid.uuid4().hex[:12]}"
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO fanout_runs (id, task, agents, context, started_at, tenant_id, caller)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, task, json.dumps(agents), json.dumps(context or {}),
                 time.time(), tenant_id, json.dumps(caller)),
            )

        # Dispatch all agents concurrently (fire and don't wait here)
        slots = []
        for agent_id in agents:
            env = self._make_envelope(task, agent_id, caller, context or {}, run_id)
            trigger_id = env["id"]
            slot_id = f"slot_{uuid.uuid4().hex[:10]}"
            with self._tx() as cur:
                cur.execute(
                    """INSERT INTO fanout_slots (id, run_id, agent_id, trigger_id)
                       VALUES (?, ?, ?, ?)""",
                    (slot_id, run_id, agent_id, trigger_id),
                )
            slots.append((agent_id, trigger_id, env))

        if self.on_trigger:
            await asyncio.gather(*[self.on_trigger(env) for _, _, env in slots])

        logger.info("Fan-out %s started: %d agents", run_id, len(agents))
        return run_id

    async def wait(self, run_id: str, timeout_sec: float = 120.0) -> dict:
        """
        Poll until all slots complete or timeout.
        Returns the full run dict with per-agent results.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            self._sync_slot_statuses(run_id)
            run = self.get_run(run_id)
            if not run:
                raise ValueError(f"Fan-out run {run_id} not found")
            if run["status"] != "running":
                return run
            await asyncio.sleep(2.0)

        # Timeout — mark run as failed
        with self._tx() as cur:
            cur.execute(
                "UPDATE fanout_runs SET status='failed', finished_at=? WHERE id=?",
                (time.time(), run_id),
            )
        return self.get_run(run_id) or {"run_id": run_id, "status": "failed", "error": "timeout"}

    def get_run(self, run_id: str) -> dict | None:
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM fanout_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            run = dict(row)
            slots = cur.execute(
                "SELECT * FROM fanout_slots WHERE run_id=?", (run_id,)
            ).fetchall()
        run["slots"] = [dict(s) for s in slots]
        return run

    def list_runs(
        self,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        sql = "SELECT * FROM fanout_runs WHERE 1=1"
        params: list = []
        if tenant_id:
            sql += " AND tenant_id=?"
            params.append(tenant_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._tx() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_envelope(
        self, task: str, agent_id: str, caller: dict, context: dict, run_id: str
    ) -> dict:
        env = from_http(
            body={
                "text": task,
                "agent_id": agent_id,
                "context": {**context, "fanout_run_id": run_id},
            },
            headers={
                "x-identity-id": caller.get("identity_id", "fanout-service"),
                "x-roles": ",".join(caller.get("roles", ["operator"])),
                "x-tenant-id": caller.get("tenant_id", "default"),
            },
            agent_id=agent_id,
        )
        return env

    def _sync_slot_statuses(self, run_id: str) -> None:
        """Pull completed trigger statuses from the triggers table into fanout_slots."""
        with self._tx() as cur:
            slots = cur.execute(
                "SELECT id, trigger_id FROM fanout_slots WHERE run_id=? AND status='running'",
                (run_id,),
            ).fetchall()

        for slot in slots:
            try:
                with self._conn() as conn:
                    trig = conn.execute(
                        "SELECT status, response FROM triggers WHERE id=?",
                        (slot["trigger_id"],),
                    ).fetchone()
                if trig and trig["status"] in ("done", "failed"):
                    with self._tx() as cur:
                        cur.execute(
                            "UPDATE fanout_slots SET status=?, response=? WHERE id=?",
                            (trig["status"], trig["response"], slot["id"]),
                        )
            except Exception as exc:
                logger.warning("Error syncing slot %s: %s", slot["id"], exc)

        # Check if all slots are done
        with self._tx() as cur:
            pending = cur.execute(
                "SELECT COUNT(*) FROM fanout_slots WHERE run_id=? AND status='running'",
                (run_id,),
            ).fetchone()[0]

        if pending == 0:
            # Determine final status: done if all slots done, else failed
            with self._tx() as cur:
                failed_count = cur.execute(
                    "SELECT COUNT(*) FROM fanout_slots WHERE run_id=? AND status='failed'",
                    (run_id,),
                ).fetchone()[0]
            final_status = "failed" if failed_count > 0 else "done"
            with self._tx() as cur:
                cur.execute(
                    "UPDATE fanout_runs SET status=?, finished_at=? WHERE id=? AND status='running'",
                    (final_status, time.time(), run_id),
                )
