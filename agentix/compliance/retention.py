"""
Data retention policy engine.

Policies are defined in YAML (config/retention.yaml) or inline:

  retention:
    default_ttl_days: 90
    policies:
      - table: agent_state
        ttl_days: 30
      - table: audit_log
        ttl_days: 365    # keep for 1 year
      - table: triggers
        ttl_days: 90
        tenant_overrides:
          enterprise_tenant: 365

Usage:
  engine = RetentionEngine.from_config(cfg, db_path="data/agentix.db")
  deleted = engine.run_once()   # purge expired rows
  # schedule engine.run_once() as a daily cron job
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Tables with timestamp columns subject to retention
_TABLE_TS_COLUMNS = {
    "triggers": "created_at",
    "agent_state": "updated_at",
    "audit_log": "timestamp",
    "cost_ledger": "timestamp",
    "pipeline_runs": "started_at",
}


@dataclass
class RetentionPolicy:
    table: str
    ttl_days: int
    tenant_overrides: dict[str, int] = field(default_factory=dict)

    def ttl_for_tenant(self, tenant_id: str) -> int:
        return self.tenant_overrides.get(tenant_id, self.ttl_days)


class RetentionEngine:
    """Applies retention policies to SQLite tables."""

    def __init__(self, db_path: str, policies: list[RetentionPolicy], default_ttl_days: int = 90) -> None:
        self._db_path = db_path
        self._default_ttl = default_ttl_days
        self._policies: dict[str, RetentionPolicy] = {p.table: p for p in policies}

    @classmethod
    def from_config(cls, cfg: dict, db_path: str) -> "RetentionEngine":
        retention_cfg = cfg.get("retention", {})
        default_ttl = retention_cfg.get("default_ttl_days", 90)
        policies = []
        for p in retention_cfg.get("policies", []):
            policies.append(RetentionPolicy(
                table=p["table"],
                ttl_days=p.get("ttl_days", default_ttl),
                tenant_overrides=p.get("tenant_overrides", {}),
            ))
        return cls(db_path=db_path, policies=policies, default_ttl_days=default_ttl)

    def run_once(self) -> dict[str, int]:
        """Purge expired rows from all policy-covered tables. Returns {table: rows_deleted}."""
        summary: dict[str, int] = {}
        if not Path(self._db_path).exists():
            return summary

        conn = sqlite3.connect(self._db_path)
        try:
            for table, ts_col in _TABLE_TS_COLUMNS.items():
                policy = self._policies.get(table)
                ttl_days = policy.ttl_days if policy else self._default_ttl
                cutoff_sec = time.time() - ttl_days * 86400
                # SQLite timestamps stored as ISO strings or Unix floats
                deleted = 0
                try:
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE {ts_col} < ?",
                        (cutoff_sec,),
                    )
                    deleted = cur.rowcount
                    conn.commit()
                except sqlite3.OperationalError:
                    # Table or column may not exist in this deployment tier
                    pass
                if deleted:
                    log.info("RetentionEngine: purged %d rows from %s (ttl=%dd)", deleted, table, ttl_days)
                    summary[table] = deleted
        finally:
            conn.close()

        return summary

    def effective_ttl(self, table: str, tenant_id: str = "default") -> int:
        policy = self._policies.get(table)
        if policy:
            return policy.ttl_for_tenant(tenant_id)
        return self._default_ttl
