"""
Eval runner — execute an eval dataset against an agent (or mock LLM).

EvalRun schema stored in SQLite:
  eval_runs    — one row per evaluation run
  eval_results — one row per (run, case) with score and actual output

Usage:
    runner = EvalRunner(db_path, llm=my_llm)
    run_id = await runner.run(
        dataset=dataset,
        agent_system_prompt="You are a helpful assistant.",
        scorers=["contains", "word_overlap"],
    )
    report = runner.get_report(run_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from agentix.eval.dataset import EvalDataset, EvalCase
from agentix.eval.scorers import Score, get_scorer

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    dataset_name    TEXT NOT NULL,
    agent_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|done|failed
    total_cases     INTEGER NOT NULL DEFAULT 0,
    passed_cases    INTEGER NOT NULL DEFAULT 0,
    failed_cases    INTEGER NOT NULL DEFAULT 0,
    pass_rate       REAL,
    scorers         TEXT NOT NULL DEFAULT '[]',
    started_at      REAL NOT NULL,
    finished_at     REAL,
    error           TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS eval_results (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    case_id     TEXT NOT NULL,
    input       TEXT NOT NULL,
    expected    TEXT NOT NULL,
    actual      TEXT,
    scores      TEXT NOT NULL DEFAULT '{}',   -- JSON {scorer: {value, passed, explanation}}
    passed      INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    FOREIGN KEY (run_id) REFERENCES eval_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_results_run ON eval_results(run_id);
"""


class EvalRunner:
    """Runs an eval dataset against an LLM and stores scored results."""

    def __init__(
        self,
        db_path: str | Path = "data/agentix.db",
        llm: Any = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.llm = llm
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

    # ------------------------------------------------------------------
    # Run execution
    # ------------------------------------------------------------------

    async def run(
        self,
        dataset: EvalDataset,
        system_prompt: str = "",
        scorers: list[str] | None = None,
        agent_id: str | None = None,
        concurrency: int = 4,
        tenant_id: str = "default",
        model: str | None = None,
    ) -> str:
        """
        Run all cases in the dataset concurrently (up to `concurrency` at once).
        Returns a run_id for later retrieval.
        """
        scorer_names = scorers or ["contains"]
        scorer_fns = {name: get_scorer(name) for name in scorer_names}

        run_id = f"evrun_{uuid.uuid4().hex[:10]}"
        with self._tx() as cur:
            cur.execute(
                """INSERT INTO eval_runs
                   (id, dataset_id, dataset_name, agent_id, total_cases, scorers, started_at, tenant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, dataset.id, dataset.name, agent_id,
                 len(dataset.cases), json.dumps(scorer_names), time.time(), tenant_id),
            )

        sem = asyncio.Semaphore(concurrency)
        tasks = [
            self._run_case(run_id, case, system_prompt, scorer_fns, sem, model)
            for case in dataset.cases
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        self._finalize_run(run_id, scorer_names)
        return run_id

    async def _run_case(
        self,
        run_id: str,
        case: EvalCase,
        system_prompt: str,
        scorer_fns: dict,
        sem: asyncio.Semaphore,
        model: str | None,
    ) -> None:
        result_id = f"evres_{uuid.uuid4().hex[:10]}"
        actual: str | None = None
        error: str | None = None
        scores: dict = {}

        async with sem:
            try:
                if self.llm:
                    response = await self.llm.complete(
                        messages=[{"role": "user", "content": case.input}],
                        system=system_prompt,
                        model=model,
                        temperature=0.0,
                    )
                    actual = response.content or ""
                else:
                    actual = ""
                    error = "No LLM configured"
            except Exception as exc:
                error = str(exc)
                actual = ""

        for name, scorer_fn in scorer_fns.items():
            try:
                score: Score = scorer_fn(actual or "", case.expected)
                scores[name] = {
                    "value": score.value,
                    "passed": score.passed,
                    "explanation": score.explanation,
                }
            except Exception as exc:
                scores[name] = {"value": 0.0, "passed": False, "explanation": str(exc)}

        # Overall pass: all scorers must pass
        overall_passed = all(s["passed"] for s in scores.values()) and not error

        with self._tx() as cur:
            cur.execute(
                """INSERT INTO eval_results
                   (id, run_id, case_id, input, expected, actual, scores, passed, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (result_id, run_id, case.id, case.input, case.expected,
                 actual, json.dumps(scores), int(overall_passed), error),
            )

    def _finalize_run(self, run_id: str, scorer_names: list[str]) -> None:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT passed, error FROM eval_results WHERE run_id=?", (run_id,)
            ).fetchall()

        total = len(rows)
        passed = sum(1 for r in rows if r["passed"])
        failed = total - passed
        pass_rate = passed / total if total > 0 else 0.0

        with self._tx() as cur:
            cur.execute(
                """UPDATE eval_runs
                   SET status='done', finished_at=?, total_cases=?, passed_cases=?,
                       failed_cases=?, pass_rate=?
                   WHERE id=?""",
                (time.time(), total, passed, failed, pass_rate, run_id),
            )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_report(self, run_id: str) -> dict | None:
        with self._tx() as cur:
            run = cur.execute("SELECT * FROM eval_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return None
            results = cur.execute(
                "SELECT * FROM eval_results WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()

        report = dict(run)
        report["scorers"] = json.loads(report.get("scorers") or "[]")
        report["results"] = []
        for r in results:
            d = dict(r)
            d["scores"] = json.loads(d.get("scores") or "{}")
            d["passed"] = bool(d["passed"])
            report["results"].append(d)
        return report

    def list_runs(self, tenant_id: str = "default", limit: int = 50) -> list[dict]:
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT * FROM eval_runs WHERE tenant_id=? ORDER BY started_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]
