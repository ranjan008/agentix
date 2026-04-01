"""
SQLite-backed state store — Lite tier.
Handles agent registry, trigger history, key-value state, and audit log.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    spec        TEXT NOT NULL,   -- JSON-serialised agent YAML spec
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS triggers (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed
    payload         TEXT NOT NULL,   -- full TriggerEnvelope JSON
    created_at      REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_triggers_agent  ON triggers(agent_id);
CREATE INDEX IF NOT EXISTS idx_triggers_status ON triggers(status);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id    TEXT NOT NULL,
    scope       TEXT NOT NULL,   -- e.g. "user:<id>" or "session:<id>"
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,   -- JSON
    ttl_until   REAL,            -- NULL = no expiry
    updated_at  REAL NOT NULL,
    PRIMARY KEY (agent_id, scope, key)
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    event_type  TEXT NOT NULL,
    trigger_id  TEXT,
    agent_id    TEXT,
    actor       TEXT,
    detail      TEXT             -- JSON
);

CREATE TABLE IF NOT EXISTS skills (
    name        TEXT PRIMARY KEY,
    version     TEXT NOT NULL,
    source      TEXT NOT NULL,   -- builtin|hub|local|git
    spec        TEXT NOT NULL,   -- JSON
    installed_at REAL NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Agent registry
    # ------------------------------------------------------------------

    def upsert_agent(self, spec: dict) -> None:
        now = time.time()
        name = spec["metadata"]["name"]
        version = spec["metadata"].get("version", "0.0.1")
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO agents (id, name, version, spec, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     version=excluded.version, spec=excluded.spec, updated_at=excluded.updated_at""",
                (name, name, version, json.dumps(spec), now, now),
            )

    def get_agent(self, agent_id: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute("SELECT spec FROM agents WHERE id=?", (agent_id,)).fetchone()
        return json.loads(row["spec"]) if row else None

    def list_agents(self) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute("SELECT id, name, version FROM agents ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Trigger tracking
    # ------------------------------------------------------------------

    def create_trigger(self, envelope: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT OR IGNORE INTO triggers
                   (id, agent_id, channel, status, payload, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (
                    envelope["id"],
                    envelope["agent_id"],
                    envelope["channel"],
                    json.dumps(envelope),
                    time.time(),
                ),
            )

    def update_trigger_status(
        self,
        trigger_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = time.time()
        with self._cursor() as cur:
            if status == "running":
                cur.execute(
                    "UPDATE triggers SET status=?, started_at=? WHERE id=?",
                    (status, now, trigger_id),
                )
            else:
                cur.execute(
                    "UPDATE triggers SET status=?, finished_at=?, error=? WHERE id=?",
                    (status, now, error, trigger_id),
                )

    def get_trigger(self, trigger_id: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM triggers WHERE id=?", (trigger_id,)
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Agent key-value state
    # ------------------------------------------------------------------

    def set_state(self, agent_id: str, scope: str, key: str, value: Any, ttl_sec: int | None = None) -> None:
        now = time.time()
        ttl_until = now + ttl_sec if ttl_sec else None
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO agent_state (agent_id, scope, key, value, ttl_until, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id, scope, key) DO UPDATE SET
                     value=excluded.value, ttl_until=excluded.ttl_until, updated_at=excluded.updated_at""",
                (agent_id, scope, key, json.dumps(value), ttl_until, now),
            )

    def get_state(self, agent_id: str, scope: str, key: str) -> Any:
        now = time.time()
        with self._cursor() as cur:
            row = cur.execute(
                """SELECT value, ttl_until FROM agent_state
                   WHERE agent_id=? AND scope=? AND key=?""",
                (agent_id, scope, key),
            ).fetchone()
        if not row:
            return None
        if row["ttl_until"] and now > row["ttl_until"]:
            return None
        return json.loads(row["value"])

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def audit(
        self,
        event_type: str,
        trigger_id: str | None = None,
        agent_id: str | None = None,
        actor: str | None = None,
        detail: dict | None = None,
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO audit_log (ts, event_type, trigger_id, agent_id, actor, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (time.time(), event_type, trigger_id, agent_id, actor, json.dumps(detail or {})),
            )

    # ------------------------------------------------------------------
    # Skills registry
    # ------------------------------------------------------------------

    def install_skill(self, name: str, version: str, source: str, spec: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO skills (name, version, source, spec, installed_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     version=excluded.version, source=excluded.source,
                     spec=excluded.spec, installed_at=excluded.installed_at""",
                (name, version, source, json.dumps(spec), time.time()),
            )

    def get_skill(self, name: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute("SELECT * FROM skills WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["spec"] = json.loads(d["spec"])
        return d

    def list_skills(self) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT name, version, source FROM skills ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]
