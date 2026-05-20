"""
Eval dataset — a collection of (input, expected_output) test cases.

Dataset schema stored in SQLite:
  eval_datasets — metadata
  eval_cases    — individual test cases

Each case has:
  input: str       — the prompt / user message
  expected: str    — the expected response (or substring / regex)
  tags: dict       — optional labels (difficulty, topic, etc.)
  metadata: dict   — free-form extra info
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_datasets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    agent_id    TEXT,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_cases (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    input       TEXT NOT NULL,
    expected    TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '{}',
    metadata    TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    FOREIGN KEY (dataset_id) REFERENCES eval_datasets(id)
);
CREATE INDEX IF NOT EXISTS idx_cases_dataset ON eval_cases(dataset_id);
"""


class EvalCase:
    def __init__(self, row: dict) -> None:
        self._row = row

    @property
    def id(self) -> str:
        return self._row["id"]

    @property
    def input(self) -> str:
        return self._row["input"]

    @property
    def expected(self) -> str:
        return self._row["expected"]

    @property
    def tags(self) -> dict:
        return json.loads(self._row.get("tags") or "{}")

    @property
    def metadata(self) -> dict:
        return json.loads(self._row.get("metadata") or "{}")

    def to_dict(self) -> dict:
        d = dict(self._row)
        d["tags"] = self.tags
        d["metadata"] = self.metadata
        return d


class EvalDataset:
    def __init__(self, row: dict, cases: list[EvalCase]) -> None:
        self._row = row
        self.cases = cases

    @property
    def id(self) -> str:
        return self._row["id"]

    @property
    def name(self) -> str:
        return self._row["name"]

    @property
    def agent_id(self) -> str | None:
        return self._row.get("agent_id")

    def to_dict(self) -> dict:
        return {
            **self._row,
            "case_count": len(self.cases),
            "cases": [c.to_dict() for c in self.cases],
        }


class DatasetStore:
    """CRUD for eval datasets and their cases."""

    def __init__(self, db_path: str | Path = "data/agentix.db") -> None:
        self.db_path = Path(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

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

    def create_dataset(
        self,
        name: str,
        agent_id: str | None = None,
        description: str | None = None,
        tenant_id: str = "default",
    ) -> str:
        did = f"evds_{uuid.uuid4().hex[:10]}"
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO eval_datasets (id, name, agent_id, description, tenant_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (did, name, agent_id, description, tenant_id, time.time()),
            )
        return did

    def add_case(
        self,
        dataset_id: str,
        input: str,
        expected: str,
        tags: dict | None = None,
        metadata: dict | None = None,
    ) -> str:
        cid = f"case_{uuid.uuid4().hex[:10]}"
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO eval_cases (id, dataset_id, input, expected, tags, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cid, dataset_id, input, expected,
                 json.dumps(tags or {}), json.dumps(metadata or {}), time.time()),
            )
        return cid

    def add_cases_bulk(self, dataset_id: str, cases: list[dict]) -> list[str]:
        """Bulk add cases. Each dict: {input, expected, tags?, metadata?}"""
        ids = []
        for c in cases:
            ids.append(self.add_case(dataset_id, c["input"], c["expected"],
                                      c.get("tags"), c.get("metadata")))
        return ids

    def get_dataset(self, dataset_id: str) -> EvalDataset | None:
        with self._tx() as cur:
            row = cur.execute("SELECT * FROM eval_datasets WHERE id=?", (dataset_id,)).fetchone()
            if not row:
                return None
            cases = cur.execute(
                "SELECT * FROM eval_cases WHERE dataset_id=? ORDER BY created_at", (dataset_id,)
            ).fetchall()
        return EvalDataset(dict(row), [EvalCase(dict(c)) for c in cases])

    def get_dataset_by_name(self, name: str, tenant_id: str = "default") -> EvalDataset | None:
        with self._tx() as cur:
            row = cur.execute(
                "SELECT * FROM eval_datasets WHERE name=? AND tenant_id=?", (name, tenant_id)
            ).fetchone()
        if not row:
            return None
        return self.get_dataset(row["id"])

    def list_datasets(self, tenant_id: str = "default") -> list[dict]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT d.*, COUNT(c.id) as case_count FROM eval_datasets d "
                "LEFT JOIN eval_cases c ON c.dataset_id=d.id "
                "WHERE d.tenant_id=? GROUP BY d.id ORDER BY d.created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_dataset(self, dataset_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM eval_cases WHERE dataset_id=?", (dataset_id,))
            cur.execute("DELETE FROM eval_datasets WHERE id=?", (dataset_id,))
