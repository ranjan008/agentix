"""
HITL Checkpoint Store — persists agent mid-run state so execution can be
resumed after a human approves, rejects, or edits a pending tool call.

Schema
------
checkpoints
  trigger_id   TEXT PK   — the trigger that is paused
  iteration    INTEGER   — loop iteration at the time of pause
  messages     TEXT      — JSON-serialised message history
  pending_calls TEXT     — JSON list of tool_use blocks awaiting approval
  created_at   REAL
  expires_at   REAL      — auto-expire after hitl.timeout_sec
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    trigger_id    TEXT PRIMARY KEY,
    iteration     INTEGER NOT NULL DEFAULT 0,
    messages      TEXT    NOT NULL DEFAULT '[]',
    pending_calls TEXT    NOT NULL DEFAULT '[]',
    created_at    REAL    NOT NULL,
    expires_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_cp_expires ON checkpoints(expires_at);
"""


@dataclass
class Checkpoint:
    trigger_id: str
    messages: list[dict]
    pending_calls: list[dict]       # tool_use blocks (name, input, id)
    iteration: int = 0
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "trigger_id": self.trigger_id,
            "iteration": self.iteration,
            "messages": self.messages,
            "pending_calls": self.pending_calls,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Checkpoint":
        return cls(
            trigger_id=row["trigger_id"],
            iteration=row["iteration"],
            messages=json.loads(row["messages"]),
            pending_calls=json.loads(row["pending_calls"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )


class CheckpointStore:
    """SQLite-backed store for HITL checkpoints."""

    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
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

    def save(self, cp: Checkpoint) -> None:
        """Upsert a checkpoint."""
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO checkpoints
                   (trigger_id, iteration, messages, pending_calls, created_at, expires_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(trigger_id) DO UPDATE SET
                       iteration=excluded.iteration,
                       messages=excluded.messages,
                       pending_calls=excluded.pending_calls,
                       expires_at=excluded.expires_at""",
                (
                    cp.trigger_id,
                    cp.iteration,
                    json.dumps(cp.messages),
                    json.dumps(cp.pending_calls),
                    cp.created_at,
                    cp.expires_at,
                ),
            )

    def load(self, trigger_id: str) -> Checkpoint | None:
        """Load a checkpoint; returns None if not found or expired."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE trigger_id=?", (trigger_id,)
            ).fetchone()
        if not row:
            return None
        cp = Checkpoint.from_row(row)
        if cp.expires_at and time.time() > cp.expires_at:
            self.delete(trigger_id)
            return None
        return cp

    def delete(self, trigger_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM checkpoints WHERE trigger_id=?", (trigger_id,))

    def purge_expired(self) -> int:
        """Remove all expired checkpoints; return count deleted."""
        with self._tx() as cur:
            cur.execute(
                "DELETE FROM checkpoints WHERE expires_at IS NOT NULL AND expires_at < ?",
                (time.time(),),
            )
            return cur.rowcount
