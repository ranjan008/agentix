"""
transfer_to_agent — built-in skill that hands off execution to another agent.

Enables agent swarm patterns where a coordinator/supervisor agent can
delegate specific subtasks to specialist agents and receive their responses.

When the coordinator calls transfer_to_agent:
  1. A new TriggerEnvelope is dispatched to the target agent.
  2. The runtime waits for the target agent to complete (polls triggers table).
  3. The target agent's response is returned as the tool result.

Tool schema:
  {
    "name": "transfer_to_agent",
    "description": "Hand off a task to a specialist agent and receive its response.",
    "input_schema": {
      "type": "object",
      "properties": {
        "agent_id": {"type": "string", "description": "The target agent to hand off to."},
        "task": {"type": "string", "description": "The task description for the target agent."},
        "context": {"type": "object", "description": "Optional context to pass to the target agent."}
      },
      "required": ["agent_id", "task"]
    }
  }
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid

logger = logging.getLogger(__name__)


async def transfer_to_agent(
    agent_id: str,
    task: str,
    context: dict | None = None,
    timeout_sec: float = 120.0,
    _caller: dict | None = None,
    _db_path: str | None = None,
) -> str:
    """
    Dispatch a task to a specialist agent and wait for its response.

    Parameters injected by the runtime (via ToolExecutor):
      _caller:  the current caller identity from the envelope
      _db_path: path to the SQLite database
    """
    db_path = _db_path or os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    caller = _caller or {"identity_id": "swarm-service", "roles": ["operator"], "tenant_id": "default"}

    from agentix.watchdog.trigger_normalizer import from_http

    trigger_id = f"trig_{uuid.uuid4().hex[:16]}"
    envelope = from_http(
        body={
            "text": task,
            "agent_id": agent_id,
            "context": {**(context or {}), "transfer_from": caller.get("identity_id", "unknown")},
            "idempotency_key": trigger_id,
        },
        headers={
            "x-identity-id": caller.get("identity_id", "swarm-service"),
            "x-roles": ",".join(caller.get("roles", ["operator"])),
            "x-tenant-id": caller.get("tenant_id", "default"),
        },
        agent_id=agent_id,
    )
    # Override the trigger ID so we can poll for it
    envelope["id"] = trigger_id

    # Spawn the target agent
    from agentix.watchdog.agent_spawner import AgentSpawner
    spawner = AgentSpawner(db_path=db_path)
    await spawner.spawn(envelope)

    logger.info("transfer_to_agent: dispatched task to '%s' (trigger=%s)", agent_id, trigger_id)

    # Poll for completion
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, response FROM triggers WHERE id=?", (trigger_id,)
            ).fetchone()
            conn.close()
            if row and row["status"] in ("done", "failed"):
                response = row["response"] or ""
                if row["status"] == "failed":
                    return f"[Agent '{agent_id}' failed]: {response}"
                return response
        except Exception as exc:
            logger.warning("transfer_to_agent: poll error: %s", exc)
        await asyncio.sleep(2.0)

    return f"[Agent '{agent_id}' timed out after {timeout_sec:.0f}s]"


INSTRUCTIONS = """
## transfer_to_agent

Use `transfer_to_agent` to delegate a specific sub-task to a specialist agent and
receive its response before continuing. This enables the coordinator + specialist
swarm pattern where you plan work and route it to the right expert.

When to use:
- You need specialised knowledge or capabilities another agent provides.
- You want to parallelize: call transfer_to_agent for each subtask, then synthesise.

Parameters:
- agent_id (required): The agent ID of the specialist (e.g. "demo-researcher").
- task (required): A clear, self-contained description of what the specialist should do.
- context (optional): Extra key/value pairs to pass to the specialist.

Always wait for the specialist's response before proceeding to the next step.
"""

TOOLS = {"transfer_to_agent": transfer_to_agent}
TOOL_SCHEMAS = []  # populated after TOOL_SCHEMA is defined below

TOOL_SCHEMA = {
    "name": "transfer_to_agent",
    "description": (
        "Hand off a specific sub-task to a specialist agent and receive its response. "
        "Use this when you need expertise that another agent provides, or to parallelize work."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "The ID of the specialist agent to delegate to.",
            },
            "task": {
                "type": "string",
                "description": "Clear description of the task for the specialist agent.",
            },
            "context": {
                "type": "object",
                "description": "Optional additional context or data to pass to the agent.",
            },
        },
        "required": ["agent_id", "task"],
    },
}

TOOL_SCHEMAS.append(TOOL_SCHEMA)
