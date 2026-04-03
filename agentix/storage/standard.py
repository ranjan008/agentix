"""
Standard Tier storage adapters — PostgreSQL + Redis.

These replace the SQLite/in-process backends when infra_tier = "standard".

PostgreSQL adapter:
  - Uses psycopg2 (sync) or asyncpg (async)
  - Implements the same interface as StateStore
  - All tables use tenant_id for row-level isolation

Redis adapter:
  - Used for short-term / working memory (TTL-based key-value)
  - Replaces the SQLite agent_state table for session data
  - Uses redis-py with connection pooling

Installation:
  pip install psycopg2-binary redis
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PostgreSQL state store
# ---------------------------------------------------------------------------

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    spec        JSONB NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_tenant ON agents(tenant_id);

CREATE TABLE IF NOT EXISTS triggers (
    id              TEXT PRIMARY KEY,
    agent_id        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    payload         JSONB NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      DOUBLE PRECISION NOT NULL,
    started_at      DOUBLE PRECISION,
    finished_at     DOUBLE PRECISION,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_triggers_agent  ON triggers(agent_id);
CREATE INDEX IF NOT EXISTS idx_triggers_tenant ON triggers(tenant_id);
CREATE INDEX IF NOT EXISTS idx_triggers_status ON triggers(status);

CREATE TABLE IF NOT EXISTS agent_state (
    agent_id    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    ttl_until   DOUBLE PRECISION,
    updated_at  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (agent_id, scope, key)
);

CREATE TABLE IF NOT EXISTS audit_chain (
    seq         BIGSERIAL PRIMARY KEY,
    ts          DOUBLE PRECISION NOT NULL,
    event_type  TEXT NOT NULL,
    trigger_id  TEXT,
    agent_id    TEXT,
    actor       TEXT,
    detail      JSONB,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_chain(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_chain(ts);

CREATE TABLE IF NOT EXISTS skills (
    name         TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    source       TEXT NOT NULL,
    spec         JSONB NOT NULL,
    installed_at DOUBLE PRECISION NOT NULL
);
"""


class PostgreSQLStateStore:
    """
    Drop-in replacement for SQLite StateStore using PostgreSQL.
    Requires: pip install psycopg2-binary
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool = None
        self._init_db()

    def _connect(self):
        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(self.dsn)
            conn.autocommit = False
            return conn
        except ImportError:
            raise ImportError("PostgreSQL backend requires 'psycopg2-binary': pip install psycopg2-binary")

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def upsert_agent(self, spec: dict) -> None:
        name = spec["metadata"]["name"]
        version = spec["metadata"].get("version", "0.0.1")
        tenant_id = spec.get("tenant_id", "default")
        now = time.time()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agents (id, name, version, spec, tenant_id, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT(id) DO UPDATE SET
                         version=EXCLUDED.version, spec=EXCLUDED.spec,
                         tenant_id=EXCLUDED.tenant_id, updated_at=EXCLUDED.updated_at""",
                    (name, name, version, json.dumps(spec), tenant_id, now, now),
                )
            conn.commit()
        finally:
            conn.close()

    def get_agent(self, agent_id: str, tenant_id: str = "default") -> dict | None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT spec FROM agents WHERE id=%s AND (tenant_id=%s OR tenant_id='default')",
                    (agent_id, tenant_id),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        return json.loads(row[0]) if row else None

    def set_state(self, agent_id: str, scope: str, key: str, value: Any, ttl_sec: int | None = None) -> None:
        now = time.time()
        ttl_until = now + ttl_sec if ttl_sec else None
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_state (agent_id, scope, key, value, ttl_until, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT(agent_id, scope, key) DO UPDATE SET
                         value=EXCLUDED.value, ttl_until=EXCLUDED.ttl_until,
                         updated_at=EXCLUDED.updated_at""",
                    (agent_id, scope, key, json.dumps(value), ttl_until, now),
                )
            conn.commit()
        finally:
            conn.close()

    def get_state(self, agent_id: str, scope: str, key: str) -> Any:
        now = time.time()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value, ttl_until FROM agent_state WHERE agent_id=%s AND scope=%s AND key=%s",
                    (agent_id, scope, key),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            return None
        if row[1] and now > row[1]:
            return None
        return json.loads(row[0])

    def create_trigger(self, envelope: dict) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO triggers (id, agent_id, channel, status, payload, tenant_id, created_at)
                       VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                       ON CONFLICT DO NOTHING""",
                    (
                        envelope["id"],
                        envelope["agent_id"],
                        envelope["channel"],
                        json.dumps(envelope),
                        envelope["caller"].get("tenant_id", "default"),
                        time.time(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def update_trigger_status(self, trigger_id: str, status: str, error: str | None = None) -> None:
        now = time.time()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                if status == "running":
                    cur.execute(
                        "UPDATE triggers SET status=%s, started_at=%s WHERE id=%s",
                        (status, now, trigger_id),
                    )
                else:
                    cur.execute(
                        "UPDATE triggers SET status=%s, finished_at=%s, error=%s WHERE id=%s",
                        (status, now, error, trigger_id),
                    )
            conn.commit()
        finally:
            conn.close()

    def install_skill(self, name: str, version: str, source: str, spec: dict) -> None:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO skills (name, version, source, spec, installed_at)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT(name) DO UPDATE SET
                         version=EXCLUDED.version, source=EXCLUDED.source,
                         spec=EXCLUDED.spec, installed_at=EXCLUDED.installed_at""",
                    (name, version, source, json.dumps(spec), time.time()),
                )
            conn.commit()
        finally:
            conn.close()

    def list_agents(self, tenant_id: str = "default") -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, version FROM agents WHERE tenant_id=%s ORDER BY name",
                    (tenant_id,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "name": r[1], "version": r[2]} for r in rows]

    def audit(self, event_type: str, trigger_id=None, agent_id=None, actor=None, detail=None, tenant_id="default") -> None:
        # Delegates to AuditLog in production
        pass


# ---------------------------------------------------------------------------
# Redis working memory
# ---------------------------------------------------------------------------

class RedisStateStore:
    """
    Redis-backed short-term (working) memory.
    Replaces SQLite agent_state for the standard tier.
    Requires: pip install redis
    """

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        self.url = url
        self._redis = None

    def _get_client(self):
        if self._redis is None:
            try:
                import redis
                self._redis = redis.from_url(self.url, decode_responses=True)
            except ImportError:
                raise ImportError("Redis backend requires 'redis': pip install redis")
        return self._redis

    def _key(self, agent_id: str, scope: str, key: str) -> str:
        return f"agentix:{agent_id}:{scope}:{key}"

    def set_state(self, agent_id: str, scope: str, key: str, value: Any, ttl_sec: int | None = None) -> None:
        r = self._get_client()
        rkey = self._key(agent_id, scope, key)
        r.set(rkey, json.dumps(value))
        if ttl_sec:
            r.expire(rkey, ttl_sec)

    def get_state(self, agent_id: str, scope: str, key: str) -> Any:
        r = self._get_client()
        raw = r.get(self._key(agent_id, scope, key))
        return json.loads(raw) if raw else None

    def delete_state(self, agent_id: str, scope: str, key: str) -> None:
        self._get_client().delete(self._key(agent_id, scope, key))

    def ping(self) -> bool:
        try:
            return self._get_client().ping()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_store(cfg: dict):
    """
    Build the appropriate state store from watchdog config.
    Returns a StateStore-compatible object.
    """
    tier = cfg.get("infra_tier", "lite")
    if tier == "lite":
        from agentix.storage.state_store import StateStore
        return StateStore(cfg.get("db_path", "data/agentix.db"))
    elif tier in ("standard", "enterprise"):
        db_url = cfg.get("database_url") or cfg.get("db_url", "")
        if not db_url:
            logger.warning("No database_url configured for standard tier — falling back to SQLite")
            from agentix.storage.state_store import StateStore
            return StateStore(cfg.get("db_path", "data/agentix.db"))
        return PostgreSQLStateStore(db_url)
    else:
        from agentix.storage.state_store import StateStore
        return StateStore(cfg.get("db_path", "data/agentix.db"))
