"""
TraceStore — SQLite-backed store for agent execution traces.

Records a tree of spans per trigger: LLM calls, tool calls, and agent
lifecycle events. Serves the admin UI traces page (LangSmith-lite view).

Schema:
  traces  — one row per trigger execution (root trace context)
  spans   — one row per event within the trace (LLM call, tool call, etc.)

Usage:
    store = TraceStore("data/agentix.db")
    trace_id = store.start_trace(trigger_id="trig_abc", agent_id="research-assistant")
    span_id  = store.start_span(trace_id, name="llm.call", input={"messages": [...]})
    store.finish_span(span_id, output={"content": "..."}, tokens=150)
    store.finish_trace(trace_id, status="done", total_tokens=150)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id              TEXT PRIMARY KEY,
    trigger_id      TEXT,
    agent_id        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    status          TEXT NOT NULL DEFAULT 'running',   -- running | done | failed
    started_at      REAL NOT NULL,
    finished_at     REAL,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    total_cost_usd  REAL NOT NULL DEFAULT 0.0,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_traces_trigger  ON traces(trigger_id);
CREATE INDEX IF NOT EXISTS idx_traces_agent    ON traces(agent_id);
CREATE INDEX IF NOT EXISTS idx_traces_started  ON traces(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_traces_tenant_started ON traces(tenant_id, started_at);

CREATE TABLE IF NOT EXISTS spans (
    id              TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL,       -- llm.call | tool.call | agent.run | ...
    started_at      REAL NOT NULL,
    finished_at     REAL,
    duration_ms     REAL,
    status          TEXT NOT NULL DEFAULT 'ok',  -- ok | error
    input           TEXT,               -- JSON
    output          TEXT,               -- JSON
    attributes      TEXT NOT NULL DEFAULT '{}',  -- JSON (model, tokens, tool_name, …)
    error           TEXT,
    FOREIGN KEY (trace_id) REFERENCES traces(id)
);
CREATE INDEX IF NOT EXISTS idx_spans_trace     ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_parent    ON spans(parent_span_id);
"""


class TraceStore:
    """
    Lightweight SQLite-backed trace/span store.

    Thread-safe reads; writes are serialised per connection.  Intended for
    single-node deployments — swap to Postgres via a compatible interface for
    multi-node.
    """

    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self):
        conn = self._conn()
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Trace lifecycle
    # ------------------------------------------------------------------

    def start_trace(
        self,
        agent_id: str,
        trigger_id: str | None = None,
        tenant_id: str = "default",
    ) -> str:
        trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO traces (id, trigger_id, agent_id, tenant_id, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (trace_id, trigger_id, agent_id, tenant_id, time.time()),
            )
        return trace_id

    def finish_trace(
        self,
        trace_id: str,
        status: str = "done",
        total_tokens: int = 0,
        total_cost_usd: float = 0.0,
        error: str | None = None,
    ) -> None:
        with self._tx() as cur:
            cur.execute(
                """UPDATE traces
                   SET status=?, finished_at=?, total_tokens=?, total_cost_usd=?, error=?
                   WHERE id=?""",
                (status, time.time(), total_tokens, total_cost_usd, error, trace_id),
            )

    # ------------------------------------------------------------------
    # Span lifecycle
    # ------------------------------------------------------------------

    def start_span(
        self,
        trace_id: str,
        name: str,
        parent_span_id: str | None = None,
        input: dict | str | None = None,
        attributes: dict | None = None,
    ) -> str:
        span_id = f"span_{uuid.uuid4().hex[:16]}"
        input_json = json.dumps(input) if isinstance(input, dict) else (input or None)
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO spans (id, trace_id, parent_span_id, name, started_at, input, attributes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    span_id, trace_id, parent_span_id, name, time.time(),
                    input_json, json.dumps(attributes or {}),
                ),
            )
        return span_id

    def finish_span(
        self,
        span_id: str,
        output: dict | str | None = None,
        status: str = "ok",
        attributes: dict | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        output_json = json.dumps(output) if isinstance(output, dict) else (output or None)
        with self._tx() as cur:
            # Compute duration from stored started_at
            row = cur.execute("SELECT started_at, attributes FROM spans WHERE id=?", (span_id,)).fetchone()
            if not row:
                return
            duration_ms = (now - row["started_at"]) * 1000

            # Merge new attributes over existing
            existing = json.loads(row["attributes"] or "{}")
            if attributes:
                existing.update(attributes)

            cur.execute(
                """UPDATE spans
                   SET finished_at=?, duration_ms=?, status=?, output=?, attributes=?, error=?
                   WHERE id=?""",
                (now, duration_ms, status, output_json, json.dumps(existing), error, span_id),
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_traces(
        self,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        since_ts: float | None = None,
    ) -> list[dict]:
        """`since_ts`, when set, excludes any trace that started before it
        — the read-path half of tenant-scoped retention: a trace past its
        plan's retention window must be invisible even before a purge
        sweep has actually deleted it (a scheduled job can lag; the read
        path shouldn't)."""
        sql = "SELECT * FROM traces WHERE 1=1"
        params: list = []
        if agent_id:
            sql += " AND agent_id=?"
            params.append(agent_id)
        if tenant_id:
            sql += " AND tenant_id=?"
            params.append(tenant_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        if since_ts is not None:
            sql += " AND started_at>=?"
            params.append(since_ts)
        sql += " ORDER BY started_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        with self._tx() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count_traces(
        self,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM traces WHERE 1=1"
        params: list = []
        if agent_id:
            sql += " AND agent_id=?"
            params.append(agent_id)
        if tenant_id:
            sql += " AND tenant_id=?"
            params.append(tenant_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        with self._tx() as cur:
            return cur.execute(sql, params).fetchone()[0]

    def get_trace(self, trace_id: str) -> dict | None:
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM traces WHERE id=?", (trace_id,)).fetchone()
        return dict(row) if row else None

    def get_spans(self, trace_id: str) -> list[dict]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY started_at ASC",
                (trace_id,),
            ).fetchall()
        spans = []
        for r in rows:
            d = dict(r)
            # Parse JSON fields for convenience
            for field in ("input", "output", "attributes"):
                if d.get(field):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            spans.append(d)
        return spans

    def get_trace_with_spans(self, trace_id: str) -> dict | None:
        trace = self.get_trace(trace_id)
        if not trace:
            return None
        trace["spans"] = self.get_spans(trace_id)
        return trace

    def get_trace_by_trigger(self, trigger_id: str) -> dict | None:
        with self._tx() as cur:
            row = cur.execute(
                "SELECT id FROM traces WHERE trigger_id=? ORDER BY started_at DESC LIMIT 1",
                (trigger_id,),
            ).fetchone()
        if not row:
            return None
        return self.get_trace_with_spans(row["id"])

    def delete_trace(self, trace_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM spans WHERE trace_id=?", (trace_id,))
            cur.execute("DELETE FROM traces WHERE id=?", (trace_id,))

    def purge_before(self, cutoff_ts: float, *, tenant_id: str | None = None) -> int:
        """Delete traces (and their spans) older than cutoff_ts. Returns
        count removed.

        `tenant_id`, when set, scopes the purge to one tenant — a pooled
        store (one SQLite file serving every tenant on a shared platform-
        key runtime) can't apply a single global cutoff correctly, since
        different tenants can be on different plans with different
        retention windows; a blind whole-file purge would either purge a
        Pro tenant's traces on Free's schedule or keep a Free tenant's
        traces past their window, depending on which cutoff got passed.
        tenant_id=None preserves the original whole-file behavior, still
        correct for a dedicated single-tenant store."""
        sql = "SELECT id FROM traces WHERE started_at < ?"
        params: list = [cutoff_ts]
        if tenant_id:
            sql += " AND tenant_id = ?"
            params.append(tenant_id)

        with self._tx() as cur:
            old = cur.execute(sql, params).fetchall()
            ids = [r["id"] for r in old]
            if ids:
                placeholders = ",".join("?" * len(ids))
                cur.execute(f"DELETE FROM spans WHERE trace_id IN ({placeholders})", ids)
                cur.execute(f"DELETE FROM traces WHERE id IN ({placeholders})", ids)
        return len(ids)
