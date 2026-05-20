"""
Tests for DAG step retry policies and conditional edges.

Covers:
  - resolver.evaluate_condition: True/False/skip/error paths
  - _fire_dag_step_with_retry: retries on failure, succeeds on Nth attempt
  - Condition=False causes step to be skipped (counted as completed)
  - continue_on_failure keeps pipeline alive despite step failure
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from agentix.scheduler.engine import Scheduler
from agentix.scheduler.resolver import evaluate_condition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler(tmp_path: Path, on_trigger=None) -> Scheduler:
    db_path = str(tmp_path / "sched.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS triggers
           (id TEXT PRIMARY KEY, status TEXT, response TEXT, payload TEXT,
            agent_id TEXT, created_at REAL, updated_at REAL)"""
    )
    conn.commit()
    conn.close()
    return Scheduler(db_path=db_path, on_trigger=on_trigger)


def _insert_trigger(db_path: str, trigger_id: str, status: str = "done") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
        "VALUES (?, ?, '', '{}', 'agent', ?, ?)",
        (trigger_id, status, time.time(), time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# evaluate_condition
# ---------------------------------------------------------------------------

def test_condition_none_returns_true():
    assert evaluate_condition(None, "step1", {}) is True


def test_condition_empty_string_returns_true():
    assert evaluate_condition("", "step1", {}) is True


def test_condition_true_literal():
    assert evaluate_condition("True", "step1", {}) is True


def test_condition_false_literal():
    assert evaluate_condition("False", "step1", {}) is False


def test_condition_checks_upstream_outputs():
    ctx = {"extract": "42 rows found"}
    assert evaluate_condition("'extract' in upstream_outputs", "transform", ctx) is True
    assert evaluate_condition("'missing' in upstream_outputs", "transform", ctx) is False


def test_condition_run_context_alias():
    ctx = {"s1": "ok"}
    assert evaluate_condition("'s1' in run_context", "s2", ctx) is True


def test_condition_numeric_comparison():
    ctx = {"score": "10"}
    assert evaluate_condition("int(upstream_outputs.get('score', '0')) > 5", "gate", ctx) is True


def test_condition_step_id_available():
    assert evaluate_condition("step_id == 'my_step'", "my_step", {}) is True


def test_condition_error_defaults_to_true():
    # SyntaxError in expression → defaults to True (don't block pipeline)
    result = evaluate_condition("this is not valid python!!!", "step", {})
    assert result is True


def test_condition_blocked_builtin_not_available():
    # __import__ is not in the safe builtins namespace
    result = evaluate_condition("__import__('os').getcwd()", "step", {})
    # Should error → default True, not raise or execute
    assert result is True


# ---------------------------------------------------------------------------
# _fire_dag_step_with_retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt(tmp_path):
    """Step fails once, then succeeds on retry."""
    db_path = str(tmp_path / "sched.db")
    call_count = [0]

    async def on_trigger(env):
        call_count[0] += 1
        trigger_id = env["id"]
        if call_count[0] == 1:
            _insert_trigger(db_path, trigger_id, status="failed")
        else:
            _insert_trigger(db_path, trigger_id, status="done")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("retry-dag", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "retry-dag", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service", "payload": "{}",
    }
    step = {
        "id": "s1", "agent": "agent-a", "depends_on": [],
        "timeout_sec": 10,
        "retry": {"max_attempts": 2, "delay_sec": 0},
    }

    result = await sched._fire_dag_step_with_retry(schedule, step, "run_001", {})
    assert isinstance(result, str)
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_retry_exhausted_raises(tmp_path):
    """All retry attempts fail → RuntimeError raised."""
    db_path = str(tmp_path / "sched.db")

    async def on_trigger(env):
        _insert_trigger(db_path, env["id"], status="failed")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("retry-fail", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "retry-fail", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service", "payload": "{}",
    }
    step = {
        "id": "s1", "agent": "agent-a", "depends_on": [],
        "timeout_sec": 5,
        "retry": {"max_attempts": 3, "delay_sec": 0},
    }

    with pytest.raises(RuntimeError, match="did not complete successfully"):
        await sched._fire_dag_step_with_retry(schedule, step, "run_002", {})


@pytest.mark.asyncio
async def test_no_retry_by_default(tmp_path):
    """Without retry config, a failing step raises immediately."""
    db_path = str(tmp_path / "sched.db")
    call_count = [0]

    async def on_trigger(env):
        call_count[0] += 1
        _insert_trigger(db_path, env["id"], status="failed")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("no-retry", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "no-retry", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service", "payload": "{}",
    }
    step = {"id": "s1", "agent": "agent-a", "depends_on": [], "timeout_sec": 5}

    with pytest.raises(RuntimeError):
        await sched._fire_dag_step_with_retry(schedule, step, "run_003", {})

    assert call_count[0] == 1  # only one attempt


# ---------------------------------------------------------------------------
# Conditional edges in _fire_dag wave loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_condition_false_skips_step(tmp_path):
    """A step with condition=False should be skipped (not dispatched)."""
    dispatched = []

    async def on_trigger(env):
        dispatched.append(env["payload"]["context"]["step_id"])
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="done")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_dag(
        "cond-dag",
        steps=[
            {"id": "always", "agent": "agent-a", "depends_on": []},
            {"id": "never", "agent": "agent-b", "depends_on": [], "condition": "False"},
        ],
        trigger_spec={"type": "one_shot", "fire_at": time.time()},
    )
    with sched._tx() as cur:
        row = cur.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    await sched._fire_dag(dict(row))

    assert "always" in dispatched
    assert "never" not in dispatched


@pytest.mark.asyncio
async def test_condition_on_upstream_output(tmp_path):
    """Step 2 condition checks step 1's output; both should run."""
    db_path = str(tmp_path / "sched.db")
    dispatched = []

    def _insert_with_response(trigger_id: str, status: str = "done", response: str = "") -> None:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, '{}', 'agent', ?, ?)",
            (trigger_id, status, response, time.time(), time.time()),
        )
        conn.commit()
        conn.close()

    async def on_trigger(env):
        step_id = env["payload"]["context"]["step_id"]
        dispatched.append(step_id)
        _insert_with_response(env["id"], status="done", response="success output from " + step_id)

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_dag(
        "cond-upstream",
        steps=[
            {"id": "step1", "agent": "a", "depends_on": [], "pass_output": True},
            {"id": "step2", "agent": "b", "depends_on": ["step1"],
             "condition": "'step1' in upstream_outputs"},
        ],
        trigger_spec={"type": "one_shot", "fire_at": time.time()},
    )
    with sched._tx() as cur:
        row = cur.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    await sched._fire_dag(dict(row))

    assert "step1" in dispatched
    assert "step2" in dispatched


# ---------------------------------------------------------------------------
# continue_on_failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_continue_on_failure_keeps_pipeline_running(tmp_path):
    """If continue_on_failure=True, subsequent steps still run after failure."""
    dispatched = []
    db_path = str(tmp_path / "sched.db")

    async def on_trigger(env):
        step_id = env["payload"]["context"]["step_id"]
        dispatched.append(step_id)
        status = "failed" if step_id == "step1" else "done"
        _insert_trigger(db_path, env["id"], status=status)

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_dag(
        "cont-fail",
        steps=[
            {"id": "step1", "agent": "a", "depends_on": [], "continue_on_failure": True},
            {"id": "step2", "agent": "b", "depends_on": ["step1"]},
        ],
        trigger_spec={"type": "one_shot", "fire_at": time.time()},
    )
    with sched._tx() as cur:
        row = cur.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    await sched._fire_dag(dict(row))

    assert "step1" in dispatched
    assert "step2" in dispatched
