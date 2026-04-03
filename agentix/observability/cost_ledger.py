"""
Cost Ledger — per-agent, per-tenant token usage and cost tracking.

Tracks:
  - Input / output tokens per LLM call
  - Estimated USD cost (configurable model pricing table)
  - Tool call counts
  - Budget limits with hard quota stops
  - Alert thresholds (soft limit → warning)

Storage: SQLite table `cost_ledger` (appended every agent run)
         + `cost_quotas` for per-tenant / per-agent budget limits.

Pricing table is configurable; ships with defaults for current Anthropic models.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default pricing (USD per million tokens, as of 2026-Q1)
# ---------------------------------------------------------------------------

_DEFAULT_PRICING: dict[str, dict] = {
    "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":  {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    # fallback
    "default":            {"input":  3.00, "output": 15.00},
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_ledger (
    id              TEXT PRIMARY KEY,
    ts              REAL NOT NULL,
    trigger_id      TEXT,
    agent_id        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    model_id        TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_cost_agent  ON cost_ledger(agent_id);
CREATE INDEX IF NOT EXISTS idx_cost_tenant ON cost_ledger(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cost_ts     ON cost_ledger(ts);

CREATE TABLE IF NOT EXISTS cost_quotas (
    id              TEXT PRIMARY KEY,   -- "agent:<name>" or "tenant:<id>"
    scope_type      TEXT NOT NULL,      -- agent | tenant
    scope_id        TEXT NOT NULL,
    period          TEXT NOT NULL,      -- daily | monthly | total
    hard_limit_usd  REAL,
    soft_limit_usd  REAL,
    created_at      REAL NOT NULL,
    UNIQUE (scope_type, scope_id, period)
);
"""

# ---------------------------------------------------------------------------
# Cost Ledger
# ---------------------------------------------------------------------------

class CostLedger:
    def __init__(
        self,
        db_path: str | Path = "data/agentix.db",
        pricing: dict | None = None,
        alert_callback=None,
    ) -> None:
        self.db_path = Path(db_path)
        self.pricing = pricing or _DEFAULT_PRICING
        self.alert_callback = alert_callback  # callable(scope, message) for soft-limit alerts
        self._init_db()

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
    # Pricing
    # ------------------------------------------------------------------

    def estimate_cost(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        prices = self.pricing.get(model_id) or self.pricing.get("default", {"input": 3.0, "output": 15.0})
        cost = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
        return round(cost, 8)

    # ------------------------------------------------------------------
    # Record usage
    # ------------------------------------------------------------------

    def record(
        self,
        agent_id: str,
        tenant_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        tool_calls: int = 0,
        trigger_id: str | None = None,
    ) -> float:
        """
        Record a usage entry. Returns the estimated cost in USD.
        Raises QuotaExceededError if a hard limit is breached.
        """
        import uuid
        cost = self.estimate_cost(model_id, input_tokens, output_tokens)

        with self._tx() as cur:
            cur.execute(
                """INSERT INTO cost_ledger
                   (id, ts, trigger_id, agent_id, tenant_id, model_id,
                    input_tokens, output_tokens, tool_calls, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, time.time(), trigger_id, agent_id, tenant_id,
                 model_id, input_tokens, output_tokens, tool_calls, cost),
            )

        logger.debug(
            "Cost recorded: agent=%s tenant=%s model=%s in=%d out=%d cost=$%.6f",
            agent_id, tenant_id, model_id, input_tokens, output_tokens, cost,
        )

        # Check quotas
        self._check_quotas(agent_id, tenant_id)
        return cost

    # ------------------------------------------------------------------
    # Quota management
    # ------------------------------------------------------------------

    def set_quota(
        self,
        scope_type: str,   # "agent" | "tenant"
        scope_id: str,
        period: str,       # "daily" | "monthly" | "total"
        hard_limit_usd: float | None = None,
        soft_limit_usd: float | None = None,
    ) -> None:
        with self._tx() as cur:
            qid = f"{scope_type}:{scope_id}:{period}"
            cur.execute(
                """INSERT INTO cost_quotas
                   (id, scope_type, scope_id, period, hard_limit_usd, soft_limit_usd, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope_type, scope_id, period) DO UPDATE SET
                     hard_limit_usd=excluded.hard_limit_usd,
                     soft_limit_usd=excluded.soft_limit_usd""",
                (qid, scope_type, scope_id, period, hard_limit_usd, soft_limit_usd, time.time()),
            )

    def _check_quotas(self, agent_id: str, tenant_id: str) -> None:
        scopes = [("agent", agent_id), ("tenant", tenant_id)]
        for scope_type, scope_id in scopes:
            with self._tx() as cur:
                quotas = cur.execute(
                    "SELECT * FROM cost_quotas WHERE scope_type=? AND scope_id=?",
                    (scope_type, scope_id),
                ).fetchall()

            for quota in quotas:
                spent = self.get_spend(scope_type, scope_id, quota["period"])
                if quota["hard_limit_usd"] and spent >= quota["hard_limit_usd"]:
                    raise QuotaExceededError(
                        f"{scope_type} '{scope_id}' exceeded hard limit "
                        f"${quota['hard_limit_usd']:.2f} ({quota['period']}): "
                        f"spent ${spent:.4f}"
                    )
                if quota["soft_limit_usd"] and spent >= quota["soft_limit_usd"]:
                    msg = (
                        f"{scope_type} '{scope_id}' reached soft limit "
                        f"${quota['soft_limit_usd']:.2f} ({quota['period']}): "
                        f"spent ${spent:.4f}"
                    )
                    logger.warning("COST ALERT: %s", msg)
                    if self.alert_callback:
                        self.alert_callback(scope_id, msg)

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_spend(self, scope_type: str, scope_id: str, period: str = "monthly") -> float:
        """Total spend for a scope within the current period window."""
        now = time.time()
        if period == "daily":
            since = now - 86_400
        elif period == "monthly":
            since = now - 30 * 86_400
        else:  # total
            since = 0.0

        col = "agent_id" if scope_type == "agent" else "tenant_id"
        with self._tx() as cur:
            row = cur.execute(
                f"SELECT COALESCE(SUM(cost_usd), 0) FROM cost_ledger WHERE {col}=? AND ts>=?",
                (scope_id, since),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def summary(
        self,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        since: float | None = None,
    ) -> dict:
        """Return aggregated usage summary."""
        clauses: list[str] = []
        params: list = []
        if tenant_id:
            clauses.append("tenant_id=?")
            params.append(tenant_id)
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if since:
            clauses.append("ts>=?")
            params.append(since)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._tx() as cur:
            row = cur.execute(
                f"""SELECT
                      COUNT(*) as calls,
                      SUM(input_tokens)  as total_input,
                      SUM(output_tokens) as total_output,
                      SUM(tool_calls)    as total_tool_calls,
                      SUM(cost_usd)      as total_cost
                    FROM cost_ledger {where}""",
                params,
            ).fetchone()

        return {
            "calls": row["calls"] or 0,
            "total_input_tokens": row["total_input"] or 0,
            "total_output_tokens": row["total_output"] or 0,
            "total_tool_calls": row["total_tool_calls"] or 0,
            "total_cost_usd": round(row["total_cost"] or 0, 6),
        }

    def top_agents_by_cost(self, tenant_id: str | None = None, limit: int = 10) -> list[dict]:
        where = "WHERE tenant_id=?" if tenant_id else ""
        params = [tenant_id] if tenant_id else []
        with self._tx() as cur:
            rows = cur.execute(
                f"""SELECT agent_id, SUM(cost_usd) as total_cost,
                           SUM(input_tokens+output_tokens) as total_tokens
                    FROM cost_ledger {where}
                    GROUP BY agent_id ORDER BY total_cost DESC LIMIT ?""",
                [*params, limit],
            ).fetchall()
        return [dict(r) for r in rows]


class QuotaExceededError(Exception):
    """Raised when an agent or tenant breaches a hard spending quota."""
