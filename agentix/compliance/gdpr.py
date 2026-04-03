"""
GDPR & Privacy Compliance Engine.

Provides:
  - right_to_erasure(identity_id)  — delete all personal data for a user
  - data_export(identity_id)       — export all user data (portability)
  - consent tracking table
  - pseudonymisation helper

The engine writes an audit record for every erasure/export operation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Tables that hold identity-linked data and the column name
_IDENTITY_TABLES: dict[str, str] = {
    "triggers": "caller",            # JSON column containing identity_id
    "audit_log": "identity_id",
    "agent_state": "scope",          # scope = "user:<identity_id>"
    "cost_ledger": "agent_id",       # indirect — best-effort
    "consent": "identity_id",
}

_SCHEMA_CONSENT = """
CREATE TABLE IF NOT EXISTS consent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'default',
    purpose TEXT NOT NULL,
    granted INTEGER NOT NULL DEFAULT 1,
    granted_at REAL NOT NULL,
    revoked_at REAL,
    metadata TEXT DEFAULT '{}'
);
"""

_SCHEMA_ERASURE_LOG = """
CREATE TABLE IF NOT EXISTS erasure_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    requested_at REAL NOT NULL,
    completed_at REAL,
    tables_affected TEXT DEFAULT '{}',
    status TEXT DEFAULT 'pending'
);
"""


class GDPREngine:
    """GDPR operations engine — erasure, export, consent, pseudonymisation."""

    def __init__(self, db_path: str, audit_log=None) -> None:
        self._db_path = db_path
        self._audit = audit_log
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        if not Path(self._db_path).exists():
            return
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(_SCHEMA_CONSENT)
            conn.execute(_SCHEMA_ERASURE_LOG)
            conn.commit()

    # ------------------------------------------------------------------
    # Right to erasure (GDPR Article 17)
    # ------------------------------------------------------------------

    def right_to_erasure(self, identity_id: str, tenant_id: str = "default") -> dict:
        """
        Delete or anonymise all rows linked to identity_id.
        Returns a summary of tables affected and rows deleted/anonymised.
        """
        requested_at = time.time()
        summary: dict[str, int] = {}

        with sqlite3.connect(self._db_path) as conn:
            # Log erasure request
            cur = conn.execute(
                "INSERT INTO erasure_log (identity_id, tenant_id, requested_at, status) VALUES (?,?,?,'in_progress')",
                (identity_id, tenant_id, requested_at),
            )
            erasure_id = cur.lastrowid

            # 1. audit_log — anonymise (replace identity_id with pseudonym)
            pseudonym = self._pseudonymise(identity_id)
            try:
                r = conn.execute(
                    "UPDATE audit_log SET identity_id=? WHERE identity_id=?",
                    (pseudonym, identity_id),
                )
                summary["audit_log"] = r.rowcount
            except sqlite3.OperationalError:
                pass

            # 2. triggers — anonymise caller JSON
            try:
                rows = conn.execute("SELECT id, caller FROM triggers").fetchall()
                updated = 0
                for row_id, caller_json in rows:
                    try:
                        caller = json.loads(caller_json or "{}")
                        if caller.get("identity_id") == identity_id:
                            caller["identity_id"] = pseudonym
                            conn.execute("UPDATE triggers SET caller=? WHERE id=?", (json.dumps(caller), row_id))
                            updated += 1
                    except Exception:
                        pass
                summary["triggers"] = updated
            except sqlite3.OperationalError:
                pass

            # 3. agent_state — delete rows scoped to this user
            try:
                r = conn.execute(
                    "DELETE FROM agent_state WHERE scope=?",
                    (f"user:{identity_id}",),
                )
                summary["agent_state"] = r.rowcount
            except sqlite3.OperationalError:
                pass

            # 4. consent — delete consent records
            try:
                r = conn.execute("DELETE FROM consent WHERE identity_id=?", (identity_id,))
                summary["consent"] = r.rowcount
            except sqlite3.OperationalError:
                pass

            # Mark erasure complete
            conn.execute(
                "UPDATE erasure_log SET completed_at=?, tables_affected=?, status='completed' WHERE id=?",
                (time.time(), json.dumps(summary), erasure_id),
            )
            conn.commit()

        if self._audit:
            self._audit.record(
                "gdpr.erasure_completed",
                trigger_id=f"erasure_{erasure_id}",
                identity_id=pseudonym,
                tenant_id=tenant_id,
                detail={"tables": summary},
            )

        log.info("GDPR erasure for %s: %s", identity_id, summary)
        return {"identity_id": pseudonym, "tables": summary, "status": "completed"}

    # ------------------------------------------------------------------
    # Data portability export (GDPR Article 20)
    # ------------------------------------------------------------------

    def data_export(self, identity_id: str, tenant_id: str = "default") -> dict:
        """
        Collect all data associated with identity_id and return as a dict.
        """
        export: dict = {"identity_id": identity_id, "exported_at": datetime.now(timezone.utc).isoformat(), "data": {}}

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row

            # audit_log
            try:
                rows = conn.execute("SELECT * FROM audit_log WHERE identity_id=?", (identity_id,)).fetchall()
                export["data"]["audit_log"] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

            # agent_state
            try:
                rows = conn.execute("SELECT * FROM agent_state WHERE scope=?", (f"user:{identity_id}",)).fetchall()
                export["data"]["agent_state"] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

            # consent
            try:
                rows = conn.execute("SELECT * FROM consent WHERE identity_id=?", (identity_id,)).fetchall()
                export["data"]["consent"] = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

        if self._audit:
            self._audit.record(
                "gdpr.data_export",
                trigger_id=f"export_{int(time.time())}",
                identity_id=identity_id,
                tenant_id=tenant_id,
            )

        return export

    # ------------------------------------------------------------------
    # Consent management
    # ------------------------------------------------------------------

    def record_consent(self, identity_id: str, tenant_id: str, purpose: str, granted: bool = True, metadata: dict | None = None) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO consent (identity_id, tenant_id, purpose, granted, granted_at, metadata) VALUES (?,?,?,?,?,?)",
                (identity_id, tenant_id, purpose, 1 if granted else 0, time.time(), json.dumps(metadata or {})),
            )
            conn.commit()

    def revoke_consent(self, identity_id: str, purpose: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE consent SET granted=0, revoked_at=? WHERE identity_id=? AND purpose=? AND granted=1",
                (time.time(), identity_id, purpose),
            )
            conn.commit()

    def has_consent(self, identity_id: str, purpose: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT granted FROM consent WHERE identity_id=? AND purpose=? ORDER BY granted_at DESC LIMIT 1",
                (identity_id, purpose),
            ).fetchone()
            return bool(row and row[0])

    # ------------------------------------------------------------------
    # Pseudonymisation
    # ------------------------------------------------------------------

    @staticmethod
    def _pseudonymise(identity_id: str) -> str:
        """One-way deterministic pseudonym: sha256(identity_id)[:16]."""
        return "anon_" + hashlib.sha256(identity_id.encode()).hexdigest()[:16]
