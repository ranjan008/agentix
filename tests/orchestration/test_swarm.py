"""
Tests for SwarmConfig, SwarmRunner, and transfer_to_agent.

Covers:
  - SwarmConfig.specialist_roster_prompt() formats specialists correctly
  - SwarmRunner.run() dispatches coordinator with swarm context
  - SwarmRunner.run() includes specialist list in coordinator task
  - SwarmRunner.run() returns done when coordinator completes
  - SwarmRunner.run() returns timeout when coordinator never finishes
  - SwarmRunner._poll_trigger() returns None when trigger absent
  - SwarmRunner._poll_trigger() returns dict when trigger done/failed
  - transfer_to_agent dispatches envelope and returns response
  - transfer_to_agent returns timeout message when agent never finishes
  - transfer_to_agent returns failure message when agent fails
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from agentix.orchestration.swarm import SwarmConfig, SwarmRunner
from agentix.skills.builtin.transfer import transfer_to_agent, TOOL_SCHEMA


def _make_mock_spawner_cls(db_path: str, status: str = "done", response: str = "ok"):
    """Return a MockSpawner class that immediately completes the trigger in DB."""
    class MockSpawner:
        def __init__(self, **kwargs):
            pass

        async def spawn(self, env):
            _complete_trigger(db_path, env["id"], status=status, response=response)

    return MockSpawner


def _make_capturing_spawner_cls(db_path: str, captured: list, status: str = "done", response: str = "ok"):
    """Return a MockSpawner class that records envelopes and completes triggers."""
    class MockSpawner:
        def __init__(self, **kwargs):
            pass

        async def spawn(self, env):
            captured.append(env)
            _complete_trigger(db_path, env["id"], status=status, response=response)

    return MockSpawner


def _make_noop_spawner_cls():
    """Return a MockSpawner class that never completes the trigger (for timeout tests)."""
    class MockSpawner:
        def __init__(self, **kwargs):
            pass

        async def spawn(self, env):
            pass  # never completes

    return MockSpawner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CALLER = {
    "identity_id": "test-user",
    "roles": ["operator"],
    "tenant_id": "default",
}


def _make_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "swarm.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS triggers
           (id TEXT PRIMARY KEY, status TEXT, response TEXT, payload TEXT,
            agent_id TEXT, created_at REAL, updated_at REAL)"""
    )
    conn.commit()
    conn.close()
    return db_path


def _complete_trigger(db_path: str, trigger_id: str, status: str = "done", response: str = "") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
        "VALUES (?, ?, ?, '{}', 'agent', ?, ?)",
        (trigger_id, status, response, time.time(), time.time()),
    )
    conn.commit()
    conn.close()


def _make_runner(tmp_path: Path, on_trigger=None) -> tuple[SwarmRunner, str]:
    db_path = _make_db(tmp_path)
    config = SwarmConfig(
        coordinator="coord-agent",
        specialists=["spec-a", "spec-b"],
        task_timeout=30.0,
        total_timeout=60.0,
    )
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    return runner, db_path


# ---------------------------------------------------------------------------
# SwarmConfig
# ---------------------------------------------------------------------------

def test_swarm_config_defaults():
    cfg = SwarmConfig(coordinator="c", specialists=["a", "b"])
    assert cfg.coordinator == "c"
    assert cfg.specialists == ["a", "b"]
    assert cfg.task_timeout == 120.0
    assert cfg.total_timeout == 600.0


def test_swarm_config_to_dict():
    cfg = SwarmConfig(coordinator="c", specialists=["a"], description="test swarm")
    d = cfg.to_dict()
    assert d["coordinator"] == "c"
    assert d["specialists"] == ["a"]
    assert d["description"] == "test swarm"


def test_specialist_roster_prompt_lists_all_specialists():
    cfg = SwarmConfig(coordinator="lead", specialists=["researcher", "writer", "editor"])
    prompt = cfg.specialist_roster_prompt()
    assert "researcher" in prompt
    assert "writer" in prompt
    assert "editor" in prompt


def test_specialist_roster_prompt_contains_transfer_instruction():
    cfg = SwarmConfig(coordinator="lead", specialists=["agent-x"])
    prompt = cfg.specialist_roster_prompt()
    assert "transfer_to_agent" in prompt


def test_specialist_roster_prompt_mentions_synthesise():
    cfg = SwarmConfig(coordinator="lead", specialists=["agent-x"])
    prompt = cfg.specialist_roster_prompt()
    assert "synthes" in prompt.lower()


def test_specialist_roster_prompt_header_line():
    cfg = SwarmConfig(coordinator="lead", specialists=["spec"])
    prompt = cfg.specialist_roster_prompt()
    first_line = prompt.splitlines()[0]
    assert "specialist" in first_line.lower()


# ---------------------------------------------------------------------------
# SwarmRunner.run() — coordinator dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_runner_dispatches_coordinator(tmp_path):
    dispatched = []
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        dispatched.append(env)
        _complete_trigger(db_path, env["id"], status="done", response="synthesis complete")

    config = SwarmConfig(coordinator="coord", specialists=["spec-1"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    await runner.run("analyse the data", caller=_CALLER)

    assert len(dispatched) == 1
    assert dispatched[0]["agent_id"] == "coord"


@pytest.mark.asyncio
async def test_runner_result_status_done(tmp_path):
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="done", response="final answer")

    config = SwarmConfig(coordinator="coord", specialists=["spec"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    assert result["status"] == "done"
    assert result["response"] == "final answer"
    assert result["coordinator"] == "coord"


@pytest.mark.asyncio
async def test_runner_result_includes_swarm_id(tmp_path):
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="done", response="ok")

    config = SwarmConfig(coordinator="coord", specialists=["s"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    assert "swarm_id" in result
    assert result["swarm_id"].startswith("swarm_")


@pytest.mark.asyncio
async def test_runner_result_includes_trigger_id(tmp_path):
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="done", response="ok")

    config = SwarmConfig(coordinator="coord", specialists=["s"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    assert "trigger_id" in result
    assert result["trigger_id"].startswith("trig_")


@pytest.mark.asyncio
async def test_runner_task_includes_specialists_in_context(tmp_path):
    db_path = _make_db(tmp_path)
    captured_env = []

    async def on_trigger(env):
        captured_env.append(env)
        _complete_trigger(db_path, env["id"], status="done", response="done")

    config = SwarmConfig(coordinator="coord", specialists=["spec-alpha", "spec-beta"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    await runner.run("task", caller=_CALLER)

    ctx = captured_env[0]["payload"]["context"]
    assert "swarm_specialists" in ctx
    assert "spec-alpha" in ctx["swarm_specialists"]
    assert "spec-beta" in ctx["swarm_specialists"]


@pytest.mark.asyncio
async def test_runner_task_includes_swarm_id_in_context(tmp_path):
    db_path = _make_db(tmp_path)
    captured_env = []

    async def on_trigger(env):
        captured_env.append(env)
        _complete_trigger(db_path, env["id"], status="done", response="done")

    config = SwarmConfig(coordinator="coord", specialists=["s"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    ctx = captured_env[0]["payload"]["context"]
    assert ctx["swarm_id"] == result["swarm_id"]


@pytest.mark.asyncio
async def test_runner_timeout_path(tmp_path):
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        pass  # never completes

    config = SwarmConfig(coordinator="coord", specialists=["s"], total_timeout=0.1)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    assert result["status"] == "timeout"
    assert "timed out" in result["response"].lower()


@pytest.mark.asyncio
async def test_runner_handles_coordinator_failure(tmp_path):
    db_path = _make_db(tmp_path)

    async def on_trigger(env):
        _complete_trigger(db_path, env["id"], status="failed", response="coordinator crashed")

    config = SwarmConfig(coordinator="coord", specialists=["s"], total_timeout=10.0)
    runner = SwarmRunner(config=config, db_path=db_path, on_trigger=on_trigger)
    result = await runner.run("task", caller=_CALLER)

    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# SwarmRunner._poll_trigger
# ---------------------------------------------------------------------------

def test_poll_trigger_returns_none_when_absent(tmp_path):
    runner, _ = _make_runner(tmp_path)
    assert runner._poll_trigger("trig_doesnotexist") is None


def test_poll_trigger_returns_none_for_running_status(tmp_path):
    runner, db_path = _make_runner(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO triggers (id, status, response, payload, agent_id, created_at, updated_at) "
        "VALUES ('trig_running', 'running', '', '{}', 'agent', ?, ?)",
        (time.time(), time.time()),
    )
    conn.commit()
    conn.close()
    assert runner._poll_trigger("trig_running") is None


def test_poll_trigger_returns_dict_when_done(tmp_path):
    runner, db_path = _make_runner(tmp_path)
    _complete_trigger(db_path, "trig_done_123", status="done", response="finished")
    result = runner._poll_trigger("trig_done_123")
    assert result is not None
    assert result["status"] == "done"
    assert result["response"] == "finished"


def test_poll_trigger_returns_dict_when_failed(tmp_path):
    runner, db_path = _make_runner(tmp_path)
    _complete_trigger(db_path, "trig_fail_456", status="failed", response="error msg")
    result = runner._poll_trigger("trig_fail_456")
    assert result is not None
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# transfer_to_agent
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_transfer_to_agent_dispatches_and_returns_response(tmp_path):
    db_path = _make_db(tmp_path)
    spawned = []
    MockSpawner = _make_capturing_spawner_cls(db_path, spawned, status="done", response="specialist done")

    with patch("agentix.watchdog.agent_spawner.AgentSpawner", MockSpawner):
        result = await transfer_to_agent(
            agent_id="spec-agent",
            task="do some research",
            _caller=_CALLER,
            _db_path=db_path,
            timeout_sec=10.0,
        )

    assert result == "specialist done"
    assert len(spawned) == 1
    assert spawned[0]["agent_id"] == "spec-agent"


@pytest.mark.asyncio
async def test_transfer_to_agent_returns_failure_message(tmp_path):
    db_path = _make_db(tmp_path)
    MockSpawner = _make_mock_spawner_cls(db_path, status="failed", response="agent error")

    with patch("agentix.watchdog.agent_spawner.AgentSpawner", MockSpawner):
        result = await transfer_to_agent(
            agent_id="spec-agent",
            task="task",
            _caller=_CALLER,
            _db_path=db_path,
            timeout_sec=10.0,
        )

    assert "failed" in result.lower()
    assert "agent error" in result


@pytest.mark.asyncio
async def test_transfer_to_agent_timeout(tmp_path):
    db_path = _make_db(tmp_path)
    MockSpawner = _make_noop_spawner_cls()

    with patch("agentix.watchdog.agent_spawner.AgentSpawner", MockSpawner):
        result = await transfer_to_agent(
            agent_id="slow-agent",
            task="task",
            _caller=_CALLER,
            _db_path=db_path,
            timeout_sec=0.1,
        )

    assert "timed out" in result.lower()
    assert "slow-agent" in result


@pytest.mark.asyncio
async def test_transfer_to_agent_sets_trigger_id(tmp_path):
    db_path = _make_db(tmp_path)
    spawned = []
    MockSpawner = _make_capturing_spawner_cls(db_path, spawned, status="done", response="ok")

    with patch("agentix.watchdog.agent_spawner.AgentSpawner", MockSpawner):
        await transfer_to_agent(
            agent_id="agent-x",
            task="task",
            _caller=_CALLER,
            _db_path=db_path,
            timeout_sec=10.0,
        )

    env = spawned[0]
    assert env["id"].startswith("trig_")
    assert env["id"] == env.get("idempotency_key")


@pytest.mark.asyncio
async def test_transfer_to_agent_passes_context(tmp_path):
    db_path = _make_db(tmp_path)
    spawned = []
    MockSpawner = _make_capturing_spawner_cls(db_path, spawned, status="done", response="ok")

    with patch("agentix.watchdog.agent_spawner.AgentSpawner", MockSpawner):
        await transfer_to_agent(
            agent_id="agent-x",
            task="task",
            context={"extra_key": "extra_value"},
            _caller=_CALLER,
            _db_path=db_path,
            timeout_sec=10.0,
        )

    ctx = spawned[0]["payload"]["context"]
    assert ctx["extra_key"] == "extra_value"
    assert "transfer_from" in ctx


# ---------------------------------------------------------------------------
# TOOL_SCHEMA
# ---------------------------------------------------------------------------

def test_tool_schema_name():
    assert TOOL_SCHEMA["name"] == "transfer_to_agent"


def test_tool_schema_required_fields():
    required = TOOL_SCHEMA["input_schema"]["required"]
    assert "agent_id" in required
    assert "task" in required


def test_tool_schema_has_context_property():
    props = TOOL_SCHEMA["input_schema"]["properties"]
    assert "context" in props
    assert props["context"]["type"] == "object"
