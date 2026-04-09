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

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'standard',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    deleted     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS connectors (
    name        TEXT PRIMARY KEY,           -- user-given unique name, e.g. "my-github"
    type        TEXT NOT NULL,              -- catalog type_name, e.g. "github"
    config      TEXT NOT NULL DEFAULT '{}', -- JSON (credentials + options)
    enabled     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|connected|error
    last_tested_at REAL,
    last_error  TEXT,
    tenant_id   TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self._in_memory = str(db_path) == ":memory:"
        self.db_path = Path(db_path)
        if not self._in_memory:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # For :memory: databases keep one persistent connection so all
        # operations share the same in-process SQLite instance.
        self._memory_conn: sqlite3.Connection | None = None
        if self._in_memory:
            self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_conn.row_factory = sqlite3.Row
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._in_memory:
            return self._memory_conn  # type: ignore[return-value]
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript(_SCHEMA)
        # Idempotent migration: add response column if missing
        try:
            conn.execute("ALTER TABLE triggers ADD COLUMN response TEXT")
            conn.commit()
        except Exception:
            pass  # column already exists
        if not self._in_memory:
            conn.close()

    @contextmanager
    def _cursor(self):
        conn = self._connect()
        try:
            cur = conn.cursor()
            yield cur
            conn.commit()
        finally:
            if not self._in_memory:
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
            rows = cur.execute("SELECT id, name, version, spec FROM agents ORDER BY name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                full = json.loads(d.pop("spec", "{}"))
                d["spec"] = full.get("spec", {})
                d["metadata"] = full.get("metadata", {})
            except Exception:
                pass
            result.append(d)
        return result

    def delete_agent(self, agent_id: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM agents WHERE id=?", (agent_id,))

    def get_agent_state(self, agent_id: str) -> dict:
        """Return all non-expired state keys for an agent as a flat dict."""
        now = time.time()
        with self._cursor() as cur:
            rows = cur.execute(
                """SELECT scope, key, value FROM agent_state
                   WHERE agent_id=? AND (ttl_until IS NULL OR ttl_until > ?)""",
                (agent_id, now),
            ).fetchall()
        return {f"{r['scope']}:{r['key']}": json.loads(r["value"]) for r in rows}

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

    def save_trigger_response(self, trigger_id: str, response: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE triggers SET response=?, status='done', finished_at=? WHERE id=?",
                (response, time.time(), trigger_id),
            )

    def get_trigger(self, trigger_id: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM triggers WHERE id=?", (trigger_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_triggers(
        self,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        with self._cursor() as cur:
            rows = cur.execute(
                f"SELECT * FROM triggers {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Expand stored payload so callers see channel/caller fields
            try:
                envelope = json.loads(d.get("payload", "{}"))
                d.update({k: v for k, v in envelope.items() if k not in d})
            except Exception:
                pass
            result.append(d)
        return result

    def trigger_stats(self, hours: int = 24) -> dict:
        since = time.time() - hours * 3600
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT status, COUNT(*) as cnt FROM triggers WHERE created_at >= ? GROUP BY status",
                (since,),
            ).fetchall()
        stats: dict[str, int] = {}
        for r in rows:
            stats[r["status"]] = r["cnt"]
        return {
            "total": sum(stats.values()),
            "running": stats.get("running", 0),
            "done": stats.get("done", 0),
            "failed": stats.get("failed", 0),
            "pending": stats.get("pending", 0),
        }

    def agent_execution_stats(self) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute(
                """SELECT agent_id,
                          COUNT(*) as total,
                          SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as succeeded,
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed
                   FROM triggers GROUP BY agent_id ORDER BY total DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

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

    def list_audit(
        self,
        tenant_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        # audit_log table has no tenant_id column in the Lite schema;
        # filter by action only if supplied, ignore tenant_id filter silently.
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("event_type LIKE ?")
            params.append(f"%{action}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        with self._cursor() as cur:
            rows = cur.execute(
                f"SELECT *, ts as timestamp, actor as identity_id FROM audit_log {where} ORDER BY seq DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

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

    # ------------------------------------------------------------------
    # Tenant management
    # ------------------------------------------------------------------

    def upsert_tenant(self, tenant_id: str, name: str, tier: str = "standard", metadata: dict | None = None) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO tenants (tenant_id, name, tier, metadata, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id) DO UPDATE SET
                     name=excluded.name, tier=excluded.tier,
                     metadata=excluded.metadata, updated_at=excluded.updated_at""",
                (tenant_id, name, tier, json.dumps(metadata or {}), now, now),
            )

    def get_tenant(self, tenant_id: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM tenants WHERE tenant_id=? AND deleted=0", (tenant_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata", "{}"))
        return d

    def list_tenants(self) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT tenant_id, name, tier, metadata, created_at FROM tenants WHERE deleted=0 ORDER BY name"
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d.get("metadata", "{}"))
            result.append(d)
        return result

    def soft_delete_tenant(self, tenant_id: str) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE tenants SET deleted=1, updated_at=? WHERE tenant_id=?",
                (time.time(), tenant_id),
            )

    # ------------------------------------------------------------------
    # Connectors
    # ------------------------------------------------------------------

    def upsert_connector(self, name: str, connector_type: str, config: dict,
                         tenant_id: str | None = None) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO connectors (name, type, config, enabled, status, tenant_id, created_at, updated_at)
                   VALUES (?, ?, ?, 1, 'pending', ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       type=excluded.type, config=excluded.config,
                       tenant_id=excluded.tenant_id, updated_at=excluded.updated_at""",
                (name, connector_type, json.dumps(config), tenant_id, now, now),
            )

    def get_connector(self, name: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM connectors WHERE name=?", (name,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["config"] = json.loads(d.get("config", "{}"))
        d["enabled"] = bool(d["enabled"])
        return d

    def list_connectors(self, tenant_id: str | None = None) -> list[dict]:
        with self._cursor() as cur:
            if tenant_id:
                rows = cur.execute(
                    "SELECT * FROM connectors WHERE tenant_id=? OR tenant_id IS NULL ORDER BY name",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = cur.execute("SELECT * FROM connectors ORDER BY name").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["config"] = json.loads(d.get("config", "{}"))
            d["enabled"] = bool(d["enabled"])
            result.append(d)
        return result

    def delete_connector(self, name: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM connectors WHERE name=?", (name,))

    def update_connector_status(self, name: str, status: str, error: str | None = None) -> None:
        now = time.time()
        with self._cursor() as cur:
            cur.execute(
                "UPDATE connectors SET status=?, last_tested_at=?, last_error=?, updated_at=? WHERE name=?",
                (status, now, error, now, name),
            )

    def set_connector_enabled(self, name: str, enabled: bool) -> None:
        with self._cursor() as cur:
            cur.execute(
                "UPDATE connectors SET enabled=?, updated_at=? WHERE name=?",
                (int(enabled), time.time(), name),
            )
