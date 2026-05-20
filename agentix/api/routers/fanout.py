"""
Fan-out router — parallel multi-agent execution.

POST /fanout/runs          — start a new fan-out run
GET  /fanout/runs          — list runs
GET  /fanout/runs/{run_id} — get run status + per-agent results
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agentix.api.deps import get_current_identity
from agentix.orchestration.fanout import FanoutRunner

router = APIRouter()


def _runner() -> FanoutRunner:
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    return FanoutRunner(db_path=db_path, on_trigger=None)


class FanoutRequest(BaseModel):
    task: str
    agents: list[str]
    context: dict | None = None


@router.post("/fanout/runs", status_code=202)
async def create_fanout_run(
    body: FanoutRequest,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    if not body.agents:
        raise HTTPException(status_code=422, detail="agents list must not be empty")

    caller = {
        "identity_id": identity.get("identity_id", identity.get("sub", "unknown")),
        "roles": identity.get("roles", ["operator"]),
        "tenant_id": identity.get("tenant_id", "default"),
    }
    runner = _runner()

    # Fan-out via AgentSpawner when on_trigger is wired at startup.
    # In standalone API mode, import and use spawner directly.
    from agentix.watchdog.agent_spawner import AgentSpawner
    import asyncio

    spawner = AgentSpawner(db_path=os.environ.get("AGENTIX_DB_PATH", "data/agentix.db"))

    async def on_trigger(env: dict) -> None:
        asyncio.create_task(spawner.spawn(env))

    runner.on_trigger = on_trigger

    run_id = await runner.start(
        task=body.task,
        agents=body.agents,
        caller=caller,
        context=body.context,
        tenant_id=caller["tenant_id"],
    )
    return {"run_id": run_id, "status": "running", "agents": body.agents}


@router.get("/fanout/runs")
async def list_fanout_runs(
    identity: Annotated[dict, Depends(get_current_identity)],
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    runs = _runner().list_runs(tenant_id=tenant_id, status=status, limit=limit, offset=offset)
    return {"runs": runs, "total": len(runs)}


@router.get("/fanout/runs/{run_id}")
async def get_fanout_run(
    run_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    run = _runner().get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Fan-out run not found")
    return run
