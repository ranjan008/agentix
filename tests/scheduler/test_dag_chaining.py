"""
Tests for DAG pipeline output chaining.

Covers:
  - _fire_dag_step injects upstream_outputs into envelope context
  - _collect_step_output reads trigger response and populates run_context
  - Full wave: step-2 envelope contains step-1's output
  - context_builder.build_messages prepends [Pipeline context] block
  - Steps without pass_output do not pollute run_context
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agentix.scheduler.engine import Scheduler
from agentix.agent_runtime.context_builder import build_messages
from tests.factories import make_envelope, make_agent_spec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_trigger(db_path: str, trigger_id: str, status: str = "done", response: str = "") -> None:
    """Insert a trigger row as if an agent subprocess wrote it."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS triggers
               (id TEXT PRIMARY KEY, status TEXT, response TEXT, payload TEXT,
                agent_id TEXT, created_at REAL, updated_at REAL)"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
            "VALUES (?, ?, ?, '{}', 'test-agent', ?, ?)",
            (trigger_id, status, response, time.time(), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def _make_scheduler(tmp_path: Path, on_trigger=None) -> Scheduler:
    db_path = str(tmp_path / "sched.db")
    # Pre-create triggers table
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS triggers
           (id TEXT PRIMARY KEY, status TEXT, response TEXT, payload TEXT,
            agent_id TEXT, created_at REAL, updated_at REAL)"""
    )
    conn.commit()
    conn.close()
    return Scheduler(db_path=db_path, on_trigger=on_trigger)


# ---------------------------------------------------------------------------
# _collect_step_output
# ---------------------------------------------------------------------------

def test_collect_step_output_reads_response(tmp_path):
    sched = _make_scheduler(tmp_path)
    _insert_trigger(str(tmp_path / "sched.db"), "trig_abc", response="Extraction complete: 42 rows")

    run_context: dict[str, str] = {}
    sched._collect_step_output("extract", "trig_abc", run_context)

    assert "extract" in run_context
    assert "42 rows" in run_context["extract"]


def test_collect_step_output_missing_trigger_is_noop(tmp_path):
    sched = _make_scheduler(tmp_path)
    run_context: dict[str, str] = {}
    sched._collect_step_output("extract", "trig_nonexistent", run_context)
    assert run_context == {}


def test_collect_step_output_empty_response_skipped(tmp_path):
    sched = _make_scheduler(tmp_path)
    _insert_trigger(str(tmp_path / "sched.db"), "trig_empty", response="")

    run_context: dict[str, str] = {}
    sched._collect_step_output("extract", "trig_empty", run_context)
    assert "extract" not in run_context


# ---------------------------------------------------------------------------
# _fire_dag_step — upstream_outputs injection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fire_dag_step_injects_upstream_outputs(tmp_path):
    captured = []

    async def on_trigger(env):
        captured.append(env)
        # Simulate agent writing "done" to DB immediately
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="done", response="step1 result")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    # Register a minimal schedule row so _make_envelope works
    sid = sched.add_one_shot("test-dag", fire_at=time.time() + 9999, agent_id="dummy")

    schedule = {
        "id": sid, "name": "test-dag", "agent_id": "extractor",
        "tenant_id": "default", "run_as_role": "scheduler-service",
        "payload": json.dumps({"text": "run extraction"}),
    }
    step = {"id": "transform", "agent": "transformer", "depends_on": ["extract"], "timeout_sec": 10}
    run_context = {"extract": "prior step output"}

    trigger_id = await sched._fire_dag_step(schedule, step, "run_001", run_context)

    assert trigger_id == captured[0]["id"]
    ctx = captured[0]["payload"]["context"]
    assert "upstream_outputs" in ctx
    assert ctx["upstream_outputs"]["extract"] == "prior step output"


@pytest.mark.asyncio
async def test_fire_dag_step_no_upstream_when_run_context_empty(tmp_path):
    captured = []

    async def on_trigger(env):
        captured.append(env)
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="done")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("dag2", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "dag2", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service",
        "payload": "{}",
    }
    step = {"id": "extract", "agent": "extractor", "depends_on": [], "timeout_sec": 10}

    await sched._fire_dag_step(schedule, step, "run_002", {})

    ctx = captured[0]["payload"]["context"]
    assert "upstream_outputs" not in ctx


@pytest.mark.asyncio
async def test_fire_dag_step_returns_trigger_id(tmp_path):
    async def on_trigger(env):
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="done")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("dag3", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "dag3", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service",
        "payload": "{}",
    }
    step = {"id": "s1", "agent": "agent-a", "depends_on": [], "timeout_sec": 10}

    result = await sched._fire_dag_step(schedule, step, "run_003", {})

    assert isinstance(result, str)
    assert result.startswith("trig_")


@pytest.mark.asyncio
async def test_fire_dag_step_raises_on_failed_status(tmp_path):
    async def on_trigger(env):
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="failed")

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_one_shot("dag4", fire_at=time.time() + 9999, agent_id="dummy")
    schedule = {
        "id": sid, "name": "dag4", "agent_id": "a",
        "tenant_id": "default", "run_as_role": "scheduler-service",
        "payload": "{}",
    }
    step = {"id": "s1", "agent": "agent-a", "depends_on": [], "timeout_sec": 5}

    with pytest.raises(RuntimeError, match="did not complete successfully"):
        await sched._fire_dag_step(schedule, step, "run_004", {})


# ---------------------------------------------------------------------------
# pass_output chaining in _fire_dag wave loop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pass_output_populates_run_context(tmp_path):
    """
    A step with pass_output:true should have its response stored in run_context
    so subsequent steps receive it as upstream_outputs.
    """
    step_outputs = {}

    async def on_trigger(env):
        sid = env["payload"]["context"].get("step_id", "unknown")
        response = f"Output from {sid}"
        _insert_trigger(str(tmp_path / "sched.db"), env["id"], status="done", response=response)
        step_outputs[sid] = env  # capture for assertions

    sched = _make_scheduler(tmp_path, on_trigger=on_trigger)
    sid = sched.add_dag(
        "chaining-dag",
        steps=[
            {"id": "extract", "agent": "extractor", "depends_on": [], "pass_output": True},
            {"id": "transform", "agent": "transformer", "depends_on": ["extract"], "pass_output": True},
            {"id": "load", "agent": "loader", "depends_on": ["transform"]},
        ],
        trigger_spec={"type": "one_shot", "fire_at": time.time()},
    )

    # Directly invoke _fire_dag on a schedule row
    with sched._tx() as cur:
        row = cur.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()
    schedule = dict(row)

    await sched._fire_dag(schedule)

    # transform step should have received extract's upstream_outputs
    transform_ctx = step_outputs["transform"]["payload"]["context"]
    assert "upstream_outputs" in transform_ctx
    assert "extract" in transform_ctx["upstream_outputs"]
    assert "Output from extract" in transform_ctx["upstream_outputs"]["extract"]

    # load step should have received transform's upstream_outputs
    load_ctx = step_outputs["load"]["payload"]["context"]
    assert "upstream_outputs" in load_ctx
    assert "transform" in load_ctx["upstream_outputs"]


# ---------------------------------------------------------------------------
# context_builder — upstream_outputs rendering
# ---------------------------------------------------------------------------

def test_build_messages_prepends_pipeline_context(store):
    envelope = make_envelope(
        text="Now transform the data.",
        context={
            "upstream_outputs": {"extract": "42 rows found"},
            "pipeline_run_id": "run_abc",
        },
    )
    spec = make_agent_spec()
    msgs = build_messages(envelope, spec, store)

    user_text = msgs[-1]["content"]
    assert "[Pipeline context" in user_text
    assert "[extract]" in user_text
    assert "42 rows found" in user_text
    # The task text must still be present
    assert "Now transform the data." in user_text


def test_build_messages_no_pipeline_context_when_absent(store):
    envelope = make_envelope(text="simple task")
    spec = make_agent_spec()
    msgs = build_messages(envelope, spec, store)

    assert "[Pipeline context" not in msgs[-1]["content"]


def test_build_messages_other_context_still_appended(store):
    envelope = make_envelope(
        text="task",
        context={
            "upstream_outputs": {"s1": "result"},
            "run_id": "run_xyz",
        },
    )
    spec = make_agent_spec()
    msgs = build_messages(envelope, spec, store)

    user_text = msgs[-1]["content"]
    # Pipeline block rendered — upstream step value appears there
    assert "[Pipeline context" in user_text
    assert "[s1]: result" in user_text
    # upstream_outputs key must NOT be echoed raw in the [Context:] block
    assert "upstream_outputs" not in user_text
    # run_id should appear in [Context:] block
    assert "run_id" in user_text


def test_build_messages_multiple_upstream_steps(store):
    envelope = make_envelope(
        text="load to warehouse",
        context={
            "upstream_outputs": {
                "extract": "row_count=1000",
                "transform": "schema_validated=true",
            }
        },
    )
    spec = make_agent_spec()
    msgs = build_messages(envelope, spec, store)

    user_text = msgs[-1]["content"]
    assert "[extract]" in user_text
    assert "[transform]" in user_text
    assert "row_count=1000" in user_text
    assert "schema_validated=true" in user_text
