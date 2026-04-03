"""
Agents management router.

GET    /agents              — list all registered agents
GET    /agents/{agent_id}   — get agent spec
POST   /agents              — register a new agent
PUT    /agents/{agent_id}   — update agent spec
DELETE /agents/{agent_id}   — deregister agent
GET    /agents/{agent_id}/state — get agent runtime state
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_store, require_admin, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


class AgentSpec(BaseModel):
    agent_id: str
    spec: dict
    metadata: dict = {}


class AgentResponse(BaseModel):
    agent_id: str
    spec: dict
    metadata: dict
    status: str = "registered"


@router.get("/agents")
async def list_agents(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
    tenant_id: str | None = None,
) -> list[dict]:
    agents = store.list_agents()
    if tenant_id:
        agents = [a for a in agents if a.get("tenant_id") == tenant_id]
    return agents


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    agent = store.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return agent


@router.post("/agents", status_code=201)
async def register_agent(
    body: AgentSpec,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    full_spec = {"metadata": {"name": body.agent_id, **body.metadata}, "spec": body.spec}
    store.upsert_agent(full_spec)
    return {"agent_id": body.agent_id, "status": "registered"}


@router.put("/agents/{agent_id}")
async def update_agent(
    agent_id: str,
    body: AgentSpec,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    existing = store.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    full_spec = {"metadata": {"name": agent_id, **body.metadata}, "spec": body.spec}
    store.upsert_agent(full_spec)
    return {"agent_id": agent_id, "status": "updated"}


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> None:
    existing = store.get_agent(agent_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    store.delete_agent(agent_id)


@router.get("/agents/{agent_id}/state")
async def get_agent_state(
    agent_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    state = store.get_agent_state(agent_id)
    return state or {"agent_id": agent_id, "state": {}}
