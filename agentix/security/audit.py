"""
Append-only Audit Log with HMAC chain.

Each log entry includes:
  - seq:          monotonic sequence number
  - ts:           Unix timestamp
  - event_type:   e.g. "trigger.received", "tool.called"
  - trigger_id / agent_id / actor
  - detail:       JSON payload
  - prev_hash:    SHA-256 of the previous entry (chain anchor)
  - entry_hash:   SHA-256 of this entry's canonical form (including prev_hash)

Tampering with any entry or removing entries breaks the chain,
detectable via AuditLog.verify_chain().
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_CHAIN_GENESIS = "0" * 64  # initial prev_hash for the first entry


def _canonical(entry: dict) -> bytes:
    """Deterministic JSON serialisation of an entry for hashing."""
    fields = ("seq", "ts", "event_type", "trigger_id", "agent_id", "actor", "detail", "prev_hash")
    ordered = {k: entry.get(k) for k in fields}
    return json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode()


def _compute_hash(entry: dict, secret: str = "") -> str:
    data = _canonical(entry)
    if secret:
        return hmac_mod.new(secret.encode(), data, hashlib.sha256).hexdigest()
    return hashlib.sha256(data).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_chain (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    event_type  TEXT    NOT NULL,
    trigger_id  TEXT,
    agent_id    TEXT,
    actor       TEXT,
    detail      TEXT,
    tenant_id   TEXT    NOT NULL DEFAULT 'default',
    prev_hash   TEXT    NOT NULL,
    entry_hash  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts       ON audit_chain(ts);
CREATE INDEX IF NOT EXISTS idx_audit_agent    ON audit_chain(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant   ON audit_chain(tenant_id);
"""


class AuditLog:
    """
    HMAC-chained, append-only audit log backed by SQLite.
    """

    def __init__(
        self,
        db_path: str | Path = "data/agentix.db",
        hmac_secret: str = "",
    ) -> None:
        self.db_path = Path(db_path)
        self.hmac_secret = hmac_secret
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _tx(self):
        conn = self._connect()
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        event_type: str,
        trigger_id: str | None = None,
        agent_id: str | None = None,
        actor: str | None = None,
        detail: dict | None = None,
        tenant_id: str = "default",
    ) -> int:
        """Append an entry to the audit chain. Returns the new seq number."""
        with self._tx() as cur:
            # Fetch last entry's hash
            last = cur.execute(
                "SELECT entry_hash FROM audit_chain ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = last["entry_hash"] if last else _CHAIN_GENESIS

            entry = {
                "seq": None,  # assigned by DB
                "ts": time.time(),
                "event_type": event_type,
                "trigger_id": trigger_id,
                "agent_id": agent_id,
                "actor": actor,
                "detail": json.dumps(detail or {}),
                "prev_hash": prev_hash,
            }
            entry_hash = _compute_hash(entry, self.hmac_secret)

            cur.execute(
                """INSERT INTO audit_chain
                   (ts, event_type, trigger_id, agent_id, actor, detail,
                    tenant_id, prev_hash, entry_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entry["ts"], event_type, trigger_id, agent_id, actor,
                    entry["detail"], tenant_id, prev_hash, entry_hash,
                ),
            )
            return cur.lastrowid

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def query(
        self,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query audit entries with optional filters. Respects tenant isolation."""
        clauses = []
        params: list = []
        if tenant_id:
            clauses.append("tenant_id = ?")
            params.append(tenant_id)
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if event_type:
            clauses.append("event_type LIKE ?")
            params.append(event_type.replace("*", "%"))
        if since:
            clauses.append("ts >= ?")
            params.append(since)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM audit_chain {where} ORDER BY seq DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        with self._tx() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Chain verification
    # ------------------------------------------------------------------

    def verify_chain(self, tenant_id: str | None = None) -> tuple[bool, str]:
        """
        Walk the entire chain (or tenant slice) and verify hash integrity.
        Returns (ok: bool, message: str).
        """
        where = "WHERE tenant_id = ?" if tenant_id else ""
        params = [tenant_id] if tenant_id else []

        with self._tx() as cur:
            rows = cur.execute(
                f"SELECT * FROM audit_chain {where} ORDER BY seq ASC", params
            ).fetchall()

        if not rows:
            return True, "Chain is empty"

        expected_prev = _CHAIN_GENESIS
        for row in rows:
            entry = dict(row)
            if entry["prev_hash"] != expected_prev:
                return False, (
                    f"Chain broken at seq={entry['seq']}: "
                    f"expected prev_hash={expected_prev[:12]}… "
                    f"got={entry['prev_hash'][:12]}…"
                )
            recomputed = _compute_hash(
                {**entry, "detail": entry["detail"]}, self.hmac_secret
            )
            if recomputed != entry["entry_hash"]:
                return False, (
                    f"Entry tampered at seq={entry['seq']}: "
                    f"hash mismatch (expected={recomputed[:12]}…)"
                )
            expected_prev = entry["entry_hash"]

        return True, f"Chain OK — {len(rows)} entries verified"
