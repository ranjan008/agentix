"""
Remediation Log — OECD Due Diligence Step 6.

Tracks identified adverse AI impacts from discovery through resolution.
Each item links back to an audit_chain.seq entry so investigators can
reconstruct the exact model call that caused the harm.

Usage:
    from agentix.compliance.remediation import RemediationLog

    rlog = RemediationLog(db_path="data/agentix.db", audit_log=audit)
    item_id = rlog.open_item(
        harm_type="BIAS",
        severity="medium",
        description="Agent suggested lower salary range for female candidates",
        owner="safety@example.com",
        audit_seq_ref=1234,         # links to the offending ai.invocation entry
    )
    # ... investigate, fix system prompt, re-eval ...
    rlog.resolve(item_id, resolution_note="Updated prompt + passed adverse_impact eval")
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS remediation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_seq_ref   INTEGER,
    discovered_at   REAL    NOT NULL,
    resolved_at     REAL,
    harm_type       TEXT    NOT NULL,
    severity        TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    owner           TEXT,
    status          TEXT    NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    metadata        TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_remediation_status   ON remediation_log(status);
CREATE INDEX IF NOT EXISTS idx_remediation_severity ON remediation_log(severity);
CREATE INDEX IF NOT EXISTS idx_remediation_tenant   ON remediation_log(tenant_id);
CREATE INDEX IF NOT EXISTS idx_remediation_seq_ref  ON remediation_log(audit_seq_ref);
"""

_VALID_HARM_TYPES = frozenset({
    "BIAS", "DISCRIMINATION", "PRIVACY", "MISINFORMATION",
    "FINANCIAL_HARM", "EMOTIONAL_HARM", "OTHER",
})
_VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_VALID_STATUSES   = frozenset({"open", "in_progress", "resolved", "closed", "wont_fix"})


class RemediationLog:
    """Append-and-update remediation tracker for OECD Step 6 compliance."""

    def __init__(self, db_path: str, audit_log=None) -> None:
        self._db_path = Path(db_path)
        self._audit = audit_log
        self.init_db()

    def init_db(self) -> None:
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def open_item(
        self,
        harm_type: str,
        severity: str,
        description: str,
        owner: str | None = None,
        audit_seq_ref: int | None = None,
        metadata: dict | None = None,
        tenant_id: str = "default",
    ) -> int:
        """Open a new remediation item. Returns the item ID."""
        if harm_type not in _VALID_HARM_TYPES:
            raise ValueError(f"harm_type must be one of {sorted(_VALID_HARM_TYPES)}")
        if severity not in _VALID_SEVERITIES:
            raise ValueError(f"severity must be one of {sorted(_VALID_SEVERITIES)}")

        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO remediation_log
                   (audit_seq_ref, discovered_at, harm_type, severity, description,
                    owner, status, metadata, tenant_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (audit_seq_ref, time.time(), harm_type, severity, description,
                 owner, "open", json.dumps(metadata or {}), tenant_id),
            )
            item_id: int = cur.lastrowid or 0
            conn.commit()

        if self._audit:
            self._audit.record(
                "remediation.opened",
                detail={"item_id": item_id, "harm_type": harm_type, "severity": severity,
                        "audit_seq_ref": audit_seq_ref},
                tenant_id=tenant_id,
            )
        log.info("Remediation #%d opened [%s/%s]: %s", item_id, harm_type, severity,
                 description[:80])
        return item_id

    def update_status(
        self,
        item_id: int,
        status: str,
        owner: str | None = None,
        resolution_note: str | None = None,
        tenant_id: str = "default",
    ) -> None:
        """Update the status (and optionally owner / resolution note) of an item."""
        if status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        resolved_at = time.time() if status in ("resolved", "closed") else None
        with self._connect() as conn:
            conn.execute(
                """UPDATE remediation_log
                   SET status=?, resolved_at=?,
                       owner=COALESCE(?, owner),
                       resolution_note=COALESCE(?, resolution_note)
                   WHERE id=?""",
                (status, resolved_at, owner, resolution_note, item_id),
            )
            conn.commit()
        if self._audit:
            self._audit.record(
                f"remediation.{status}",
                detail={"item_id": item_id, "resolution_note": resolution_note},
                tenant_id=tenant_id,
            )

    def resolve(self, item_id: int, resolution_note: str,
                owner: str | None = None, tenant_id: str = "default") -> None:
        """Shorthand: mark item resolved with a mandatory resolution note."""
        self.update_status(item_id, "resolved", owner=owner,
                           resolution_note=resolution_note, tenant_id=tenant_id)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_open(self, tenant_id: str | None = None,
                  severity: str | None = None) -> list[dict]:
        """Return open/in-progress items, newest first."""
        clauses = ["status IN ('open', 'in_progress')"]
        params: list = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM remediation_log WHERE {' AND '.join(clauses)}"
                " ORDER BY discovered_at DESC",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, item_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM remediation_log WHERE id=?",
                               (item_id,)).fetchone()
        return dict(row) if row else None

    def summary(self, tenant_id: str | None = None) -> dict:
        """Counts by status and severity — handy for dashboards."""
        where  = "WHERE tenant_id = ?" if tenant_id else ""
        params = [tenant_id] if tenant_id else []
        with self._connect() as conn:
            by_status   = conn.execute(
                f"SELECT status, COUNT(*) AS n FROM remediation_log {where} GROUP BY status",
                params).fetchall()
            by_severity = conn.execute(
                f"SELECT severity, COUNT(*) AS n FROM remediation_log {where} GROUP BY severity",
                params).fetchall()
        return {
            "by_status":   {r["status"]:   r["n"] for r in by_status},
            "by_severity": {r["severity"]: r["n"] for r in by_severity},
        }
