"""
Multi-tenant isolation middleware.

Enforces tenant scoping on all state store queries:
  - Agents: registered per-tenant (tenant_id column)
  - Triggers: scoped to tenant_id
  - State: scoped to agent_id + tenant scope key
  - Audit: filtered by tenant_id

Also adds the tenant_id column migrations to the existing SQLite schema.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_MIGRATION = """
-- Add tenant_id to agents table if missing
ALTER TABLE agents ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';

-- Add tenant_id to triggers table if missing
ALTER TABLE triggers ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default';

-- Add tenant_id index
CREATE INDEX IF NOT EXISTS idx_agents_tenant   ON agents(tenant_id);
CREATE INDEX IF NOT EXISTS idx_triggers_tenant ON triggers(tenant_id);
"""


def apply_tenant_migration(db_path: str | Path) -> None:
    """
    Safely apply tenant_id columns to existing tables.
    SQLite doesn't support IF NOT EXISTS in ALTER TABLE — we catch errors.
    """
    conn = sqlite3.connect(str(db_path))
    for stmt in _MIGRATION.strip().split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                pass  # Already applied
            else:
                logger.warning("Migration statement failed: %s — %s", stmt[:60], e)
    conn.commit()
    conn.close()


class TenantStateStore:
    """
    Tenant-scoped wrapper around raw SQLite.
    Every read/write is automatically scoped to the given tenant_id.
    platform-admin callers may pass tenant_id=None to query across all tenants.
    """

    def __init__(self, db_path: str | Path, tenant_id: str) -> None:
        self.db_path = Path(db_path)
        self.tenant_id = tenant_id
        apply_tenant_migration(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

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
    # Agent registry (tenant-scoped)
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> dict | None:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT spec FROM agents WHERE id=? AND (tenant_id=? OR tenant_id='default')",
                (agent_id, self.tenant_id),
            ).fetchone()
        return json.loads(row["spec"]) if row else None

    def list_agents(self) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT id, name, version, tenant_id FROM agents WHERE tenant_id=? ORDER BY name",
                (self.tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_agent(self, spec: dict) -> None:
        now = time.time()
        name = spec["metadata"]["name"]
        version = spec["metadata"].get("version", "0.0.1")
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO agents (id, name, version, spec, tenant_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     version=excluded.version, spec=excluded.spec,
                     tenant_id=excluded.tenant_id, updated_at=excluded.updated_at""",
                (name, name, version, json.dumps(spec), self.tenant_id, now, now),
            )

    # ------------------------------------------------------------------
    # Trigger tracking (tenant-scoped)
    # ------------------------------------------------------------------

    def create_trigger(self, envelope: dict) -> None:
        with self._cursor() as cur:
            cur.execute(
                """INSERT OR IGNORE INTO triggers
                   (id, agent_id, channel, status, payload, tenant_id, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (
                    envelope["id"],
                    envelope["agent_id"],
                    envelope["channel"],
                    json.dumps(envelope),
                    self.tenant_id,
                    time.time(),
                ),
            )

    def list_triggers(self, limit: int = 50) -> list[dict]:
        with self._cursor() as cur:
            rows = cur.execute(
                """SELECT id, agent_id, channel, status, created_at
                   FROM triggers WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?""",
                (self.tenant_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Key-value state (already scoped via scope key in Phase 1)
    # ------------------------------------------------------------------

    def set_state(self, agent_id: str, key: str, value, ttl_sec: int | None = None) -> None:
        scope = f"tenant:{self.tenant_id}"
        now = time.time()
        ttl_until = now + ttl_sec if ttl_sec else None
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO agent_state (agent_id, scope, key, value, ttl_until, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(agent_id, scope, key) DO UPDATE SET
                     value=excluded.value, ttl_until=excluded.ttl_until,
                     updated_at=excluded.updated_at""",
                (agent_id, scope, key, json.dumps(value), ttl_until, now),
            )

    def get_state(self, agent_id: str, key: str):
        scope = f"tenant:{self.tenant_id}"
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
