"""
Tests for the FanoutRunner — parallel fan-out orchestration.

Covers:
  - start() creates run record and dispatches triggers
  - wait() polls and returns results when all slots complete
  - _sync_slot_statuses() picks up trigger completion from DB
  - list_runs() and get_run() queries
  - Empty agents list raises
  - Timeout path marks run as failed
  - Mixed done/failed slots → run status "failed"
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from agentix.orchestration.fanout import FanoutRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CALLER = {
    "identity_id": "test-user",
    "roles": ["operator"],
    "tenant_id": "default",
}


def _make_runner(tmp_path: Path, on_trigger=None) -> FanoutRunner:
    db_path = str(tmp_path / "fanout.db")
    # Pre-create triggers table so sync can query it
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS triggers
           (id TEXT PRIMARY KEY, status TEXT, response TEXT, payload TEXT,
            agent_id TEXT, created_at REAL, updated_at REAL)"""
    )
    conn.commit()
    conn.close()
    return FanoutRunner(db_path=db_path, on_trigger=on_trigger)


def _complete_trigger(db_path: str, trigger_id: str, status: str = "done", response: str = "") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
        "VALUES (?, ?, ?, '{}', 'agent', ?, ?)",
        (trigger_id, status, response, time.time(), time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# start()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_creates_run_record(tmp_path):
    dispatched = []

    async def on_trigger(env):
        dispatched.append(env)

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("analyse Q3", ["agent-a", "agent-b"], _CALLER)

    assert run_id.startswith("fanout_")
    run = runner.get_run(run_id)
    assert run is not None
    assert run["status"] == "running"
    assert json.loads(run["agents"]) == ["agent-a", "agent-b"]


@pytest.mark.asyncio
async def test_start_dispatches_one_trigger_per_agent(tmp_path):
    dispatched = []

    async def on_trigger(env):
        dispatched.append(env["agent_id"])

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    await runner.start("task", ["a", "b", "c"], _CALLER)

    assert sorted(dispatched) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_start_empty_agents_raises(tmp_path):
    runner = _make_runner(tmp_path)
    with pytest.raises(ValueError, match="must not be empty"):
        await runner.start("task", [], _CALLER)


@pytest.mark.asyncio
async def test_start_injects_fanout_run_id_in_context(tmp_path):
    captured = []

    async def on_trigger(env):
        captured.append(env)

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["agent-x"], _CALLER)

    ctx = captured[0]["payload"]["context"]
    assert ctx.get("fanout_run_id") == run_id


# ---------------------------------------------------------------------------
# _sync_slot_statuses + wait()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wait_returns_done_when_all_slots_complete(tmp_path):
    db_path = str(tmp_path / "fanout.db")
    trigger_ids = []

    async def on_trigger(env):
        trigger_ids.append(env["id"])
        _complete_trigger(db_path, env["id"], status="done", response=f"result from {env['agent_id']}")

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["a", "b"], _CALLER)
    result = await runner.wait(run_id, timeout_sec=10)

    assert result["status"] == "done"
    assert len(result["slots"]) == 2


@pytest.mark.asyncio
async def test_wait_run_failed_when_some_slots_fail(tmp_path):
    db_path = str(tmp_path / "fanout.db")
    call_count = [0]

    async def on_trigger(env):
        call_count[0] += 1
        status = "done" if call_count[0] == 1 else "failed"
        _complete_trigger(db_path, env["id"], status=status)

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["a", "b"], _CALLER)
    result = await runner.wait(run_id, timeout_sec=10)

    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_wait_times_out_and_marks_failed(tmp_path):
    async def on_trigger(env):
        pass  # never completes trigger

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["a"], _CALLER)
    result = await runner.wait(run_id, timeout_sec=0.1)

    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# get_run + list_runs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_run_returns_slots(tmp_path):
    async def on_trigger(env):
        pass

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["a", "b", "c"], _CALLER)
    run = runner.get_run(run_id)

    assert run is not None
    assert len(run["slots"]) == 3
    agent_ids = {s["agent_id"] for s in run["slots"]}
    assert agent_ids == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_get_run_nonexistent_returns_none(tmp_path):
    runner = _make_runner(tmp_path)
    assert runner.get_run("fanout_doesnotexist") is None


@pytest.mark.asyncio
async def test_list_runs_returns_all(tmp_path):
    async def on_trigger(env):
        pass

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    await runner.start("task1", ["a"], _CALLER)
    await runner.start("task2", ["b"], _CALLER)

    runs = runner.list_runs()
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_list_runs_filter_by_status(tmp_path):
    db_path = str(tmp_path / "fanout.db")

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="done")

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("done-task", ["a"], _CALLER)
    await runner.wait(run_id, timeout_sec=10)

    # Start another that we don't complete
    async def no_complete(env):
        pass

    runner2 = FanoutRunner(db_path=db_path, on_trigger=no_complete)
    await runner2.start("pending-task", ["b"], _CALLER)

    done_runs = runner.list_runs(status="done")
    running_runs = runner.list_runs(status="running")
    assert len(done_runs) == 1
    assert len(running_runs) == 1


@pytest.mark.asyncio
async def test_list_runs_tenant_isolation(tmp_path):
    async def on_trigger(env):
        pass

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    caller_a = {**_CALLER, "tenant_id": "tenant-a"}
    caller_b = {**_CALLER, "tenant_id": "tenant-b"}
    await runner.start("task", ["x"], caller_a, tenant_id="tenant-a")
    await runner.start("task", ["y"], caller_b, tenant_id="tenant-b")

    a_runs = runner.list_runs(tenant_id="tenant-a")
    b_runs = runner.list_runs(tenant_id="tenant-b")
    assert len(a_runs) == 1
    assert len(b_runs) == 1


# ---------------------------------------------------------------------------
# slot response content
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slot_response_captured(tmp_path):
    db_path = str(tmp_path / "fanout.db")

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="done", response="agent result here")

    runner = _make_runner(tmp_path, on_trigger=on_trigger)
    run_id = await runner.start("task", ["agent-x"], _CALLER)
    result = await runner.wait(run_id, timeout_sec=10)

    slot = result["slots"][0]
    assert slot["response"] == "agent result here"
