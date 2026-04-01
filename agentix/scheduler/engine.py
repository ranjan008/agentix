"""
Scheduler Engine — cron, one-shot, and DAG pipeline scheduling.

Persists schedules in SQLite. Runs as a background asyncio task inside
the watchdog. Fires TriggerEnvelopes directly into the watchdog's
_handle_trigger pipeline.

Schedule types:
  cron     — standard cron expression (5-field or 6-field with seconds)
  one_shot — fire once at an absolute UTC datetime
  dag      — multi-step pipeline with dependency graph

Storage schema is added to the existing agentix.db.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    type        TEXT NOT NULL,          -- cron | one_shot | dag
    spec        TEXT NOT NULL,          -- JSON (expression/datetime/steps)
    agent_id    TEXT,                   -- for cron/one_shot
    payload     TEXT NOT NULL DEFAULT '{}',
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    run_as_role TEXT NOT NULL DEFAULT 'scheduler-service',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run_at REAL,
    next_run_at REAL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              TEXT PRIMARY KEY,
    schedule_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
    step_states     TEXT NOT NULL DEFAULT '{}',       -- JSON {step_id: status}
    started_at      REAL NOT NULL,
    finished_at     REAL,
    error           TEXT,
    FOREIGN KEY (schedule_id) REFERENCES schedules(id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_schedule ON pipeline_runs(schedule_id);
"""

# ---------------------------------------------------------------------------
# Cron parser (minimal — handles standard 5-field expressions)
# ---------------------------------------------------------------------------

def _cron_next(expression: str, after: float | None = None) -> float:
    """
    Compute the next fire time (Unix timestamp) for a cron expression.
    Requires: pip install croniter
    Falls back to a simple 60-second interval if croniter is unavailable.
    """
    base = datetime.fromtimestamp(after or time.time(), tz=timezone.utc)
    try:
        from croniter import croniter
        itr = croniter(expression, base)
        return itr.get_next(float)
    except ImportError:
        logger.warning("croniter not installed — schedule fires every 60s. pip install croniter")
        return (after or time.time()) + 60.0


# ---------------------------------------------------------------------------
# DAG resolver
# ---------------------------------------------------------------------------

class DAGResolver:
    """
    Topological sort + dependency tracking for pipeline steps.

    Step spec:
      {"id": "extract", "agent": "data-extractor", "depends_on": []}
      {"id": "transform", "agent": "data-transformer", "depends_on": ["extract"]}
    """

    @staticmethod
    def topo_sort(steps: list[dict]) -> list[list[dict]]:
        """
        Return steps grouped into execution waves (parallel within each wave).
        Wave 0 = no dependencies, Wave 1 = depends only on Wave 0, etc.
        """
        step_map = {s["id"]: s for s in steps}
        remaining = {s["id"] for s in steps}
        done: set[str] = set()
        waves: list[list[dict]] = []

        while remaining:
            wave = [
                step_map[sid]
                for sid in remaining
                if all(dep in done for dep in step_map[sid].get("depends_on", []))
            ]
            if not wave:
                raise ValueError(f"DAG has a cycle or unresolvable dependency. Remaining: {remaining}")
            for s in wave:
                remaining.remove(s["id"])
                done.add(s["id"])
            waves.append(wave)

        return waves

    @staticmethod
    def ready_steps(steps: list[dict], completed: set[str], failed: set[str]) -> list[dict]:
        """Return steps whose dependencies are all completed and are not yet started."""
        started_or_done = completed | failed
        return [
            s for s in steps
            if s["id"] not in started_or_done
            and all(dep in completed for dep in s.get("depends_on", []))
            and not any(dep in failed for dep in s.get("depends_on", []))
        ]


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class Scheduler:
    """
    Async scheduler that runs as a background task inside the watchdog.
    Polls every `tick_sec` seconds for due schedules.
    """

    def __init__(
        self,
        db_path: str | Path = "data/agentix.db",
        tick_sec: float = 5.0,
        on_trigger: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.tick_sec = tick_sec
        self.on_trigger = on_trigger
        self._stop = asyncio.Event()
        self._init_db()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

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
    # CRUD
    # ------------------------------------------------------------------

    def add_cron(
        self,
        name: str,
        expression: str,
        agent_id: str,
        payload: dict | None = None,
        tenant_id: str = "default",
        run_as_role: str = "scheduler-service",
        timezone: str = "UTC",
    ) -> str:
        sid = f"sched_{uuid.uuid4().hex[:12]}"
        next_run = _cron_next(expression)
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO schedules
                   (id, name, type, spec, agent_id, payload, tenant_id, run_as_role, next_run_at, created_at)
                   VALUES (?, ?, 'cron', ?, ?, ?, ?, ?, ?, ?)""",
                (sid, name, json.dumps({"expression": expression, "timezone": timezone}),
                 agent_id, json.dumps(payload or {}), tenant_id, run_as_role, next_run, time.time()),
            )
        logger.info("Cron schedule registered: %s (%s) → %s", name, expression, agent_id)
        return sid

    def add_one_shot(
        self,
        name: str,
        fire_at: float,
        agent_id: str,
        payload: dict | None = None,
        tenant_id: str = "default",
        run_as_role: str = "scheduler-service",
    ) -> str:
        sid = f"sched_{uuid.uuid4().hex[:12]}"
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO schedules
                   (id, name, type, spec, agent_id, payload, tenant_id, run_as_role, next_run_at, created_at)
                   VALUES (?, ?, 'one_shot', ?, ?, ?, ?, ?, ?, ?)""",
                (sid, name, json.dumps({"fire_at": fire_at}),
                 agent_id, json.dumps(payload or {}), tenant_id, run_as_role, fire_at, time.time()),
            )
        logger.info("One-shot schedule registered: %s at %s → %s", name, fire_at, agent_id)
        return sid

    def add_dag(
        self,
        name: str,
        steps: list[dict],
        trigger_spec: dict,
        tenant_id: str = "default",
        run_as_role: str = "scheduler-service",
    ) -> str:
        """
        steps: list of {id, agent, depends_on: []}
        trigger_spec: {type: "cron", expression: "..."} or {type: "one_shot", fire_at: <ts>}
        """
        # Validate DAG has no cycles
        DAGResolver.topo_sort(steps)
        sid = f"sched_{uuid.uuid4().hex[:12]}"

        if trigger_spec["type"] == "cron":
            next_run = _cron_next(trigger_spec["expression"])
        else:
            next_run = trigger_spec.get("fire_at", time.time() + 60)

        with self._tx() as cur:
            cur.execute(
                """INSERT INTO schedules
                   (id, name, type, spec, payload, tenant_id, run_as_role, next_run_at, created_at)
                   VALUES (?, ?, 'dag', ?, '{}', ?, ?, ?, ?)""",
                (sid, name,
                 json.dumps({"steps": steps, "trigger": trigger_spec}),
                 tenant_id, run_as_role, next_run, time.time()),
            )
        logger.info("DAG pipeline registered: %s (%d steps)", name, len(steps))
        return sid

    def list_schedules(self) -> list[dict]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT id, name, type, agent_id, enabled, next_run_at FROM schedules ORDER BY next_run_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def enable(self, schedule_id: str, enabled: bool = True) -> None:
        with self._tx() as cur:
            cur.execute("UPDATE schedules SET enabled=? WHERE id=?", (int(enabled), schedule_id))

    def delete(self, schedule_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))

    # ------------------------------------------------------------------
    # Tick loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        logger.info("Scheduler started (tick=%.1fs)", self.tick_sec)
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Scheduler tick error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_sec)
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()

    async def _tick(self) -> None:
        now = time.time()
        with self._tx() as cur:
            due = cur.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run_at <= ?", (now,)
            ).fetchall()

        for row in due:
            schedule = dict(row)
            asyncio.create_task(self._fire(schedule))

    async def _fire(self, schedule: dict) -> None:
        stype = schedule["type"]
        try:
            if stype == "cron":
                await self._fire_cron(schedule)
            elif stype == "one_shot":
                await self._fire_one_shot(schedule)
            elif stype == "dag":
                await self._fire_dag(schedule)
        except Exception as exc:
            logger.exception("Schedule fire error [%s]: %s", schedule["name"], exc)

    async def _fire_cron(self, schedule: dict) -> None:
        spec = json.loads(schedule["spec"])
        envelope = self._make_envelope(schedule)
        logger.info("Cron firing: %s → agent=%s", schedule["name"], schedule["agent_id"])
        if self.on_trigger:
            await self.on_trigger(envelope)
        # Advance next_run_at
        next_run = _cron_next(spec["expression"])
        with self._tx() as cur:
            cur.execute(
                "UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                (time.time(), next_run, schedule["id"]),
            )

    async def _fire_one_shot(self, schedule: dict) -> None:
        envelope = self._make_envelope(schedule)
        logger.info("One-shot firing: %s → agent=%s", schedule["name"], schedule["agent_id"])
        if self.on_trigger:
            await self.on_trigger(envelope)
        # Disable after firing
        with self._tx() as cur:
            cur.execute(
                "UPDATE schedules SET enabled=0, last_run_at=? WHERE id=?",
                (time.time(), schedule["id"]),
            )

    async def _fire_dag(self, schedule: dict) -> None:
        spec = json.loads(schedule["spec"])
        steps = spec["steps"]
        trigger_spec = spec["trigger"]

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO pipeline_runs (id, schedule_id, status, step_states, started_at) VALUES (?,?,?,?,?)",
                (run_id, schedule["id"], "running", "{}", time.time()),
            )

        logger.info("DAG pipeline starting: %s run=%s (%d steps)", schedule["name"], run_id, len(steps))
        completed: set[str] = set()
        failed: set[str] = set()

        while True:
            ready = DAGResolver.ready_steps(steps, completed, failed)
            if not ready:
                break

            # Fire all ready steps in parallel
            results = await asyncio.gather(
                *[self._fire_dag_step(schedule, step, run_id) for step in ready],
                return_exceptions=True,
            )

            for step, result in zip(ready, results):
                if isinstance(result, Exception):
                    logger.error("DAG step failed: %s — %s", step["id"], result)
                    failed.add(step["id"])
                else:
                    completed.add(step["id"])

            # Update step states in DB
            states = {s: "completed" for s in completed}
            states.update({s: "failed" for s in failed})
            with self._tx() as cur:
                cur.execute(
                    "UPDATE pipeline_runs SET step_states=? WHERE id=?",
                    (json.dumps(states), run_id),
                )

            # If all remaining steps have failed deps, abort
            all_ids = {s["id"] for s in steps}
            if (completed | failed) == all_ids:
                break

        final_status = "done" if not failed else "failed"
        with self._tx() as cur:
            cur.execute(
                "UPDATE pipeline_runs SET status=?, finished_at=? WHERE id=?",
                (final_status, time.time(), run_id),
            )

        # Advance / disable schedule
        if trigger_spec["type"] == "cron":
            next_run = _cron_next(trigger_spec["expression"])
            with self._tx() as cur:
                cur.execute(
                    "UPDATE schedules SET last_run_at=?, next_run_at=? WHERE id=?",
                    (time.time(), next_run, schedule["id"]),
                )
        else:
            with self._tx() as cur:
                cur.execute(
                    "UPDATE schedules SET enabled=0, last_run_at=? WHERE id=?",
                    (time.time(), schedule["id"]),
                )

        logger.info("DAG pipeline %s: run=%s status=%s", schedule["name"], run_id, final_status)

    async def _fire_dag_step(self, schedule: dict, step: dict, run_id: str) -> None:
        envelope = self._make_envelope(schedule, override_agent=step["agent"])
        envelope["payload"]["context"]["pipeline_run_id"] = run_id
        envelope["payload"]["context"]["step_id"] = step["id"]
        logger.info("DAG step firing: %s/%s → agent=%s", schedule["name"], step["id"], step["agent"])
        if self.on_trigger:
            await self.on_trigger(envelope)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_envelope(self, schedule: dict, override_agent: str | None = None) -> dict:
        from agentix.watchdog.trigger_normalizer import from_scheduler
        return from_scheduler({
            "name": schedule["name"],
            "agent": override_agent or schedule.get("agent_id", ""),
            "tenant_id": schedule.get("tenant_id", "default"),
            "run_as_role": schedule.get("run_as_role", "scheduler-service"),
            "payload": json.loads(schedule.get("payload", "{}")),
        })
