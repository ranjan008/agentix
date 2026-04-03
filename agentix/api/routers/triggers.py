"""
Triggers router.

GET    /triggers                     — list recent triggers (with filter params)
GET    /triggers/{trigger_id}        — get trigger detail
POST   /triggers/{agent_id}/replay   — replay a trigger
GET    /triggers/live                — SSE stream of live trigger events
"""
from __future__ import annotations

import asyncio
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from agentix.api.deps import get_store, require_admin, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


@router.get("/triggers")
async def list_triggers(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
) -> dict:
    triggers = store.list_triggers(agent_id=agent_id, status=status, limit=limit, offset=offset)
    return {"triggers": triggers, "limit": limit, "offset": offset}


@router.get("/triggers/{trigger_id}")
async def get_trigger(
    trigger_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    trigger = store.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    return trigger


@router.post("/triggers/{trigger_id}/replay", status_code=202)
async def replay_trigger(
    trigger_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    trigger = store.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail=f"Trigger '{trigger_id}' not found")
    import uuid, json as _json
    new_id = f"trig_{uuid.uuid4().hex[:16]}"
    # payload column holds the original envelope JSON
    try:
        envelope = _json.loads(trigger.get("payload", "{}"))
    except Exception:
        envelope = {}
    envelope["id"] = new_id
    envelope["replay_of"] = trigger_id
    store.create_trigger(envelope)
    return {"trigger_id": new_id, "status": "queued", "replay_of": trigger_id}


@router.get("/triggers/live")
async def live_triggers(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
):
    """Server-Sent Events stream of trigger status changes."""

    async def event_stream():
        seen: set[str] = set()
        while True:
            triggers = store.list_triggers(limit=20, offset=0)
            for t in triggers:
                key = f"{t.get('id')}:{t.get('status')}"
                if key not in seen:
                    seen.add(key)
                    yield f"data: {json.dumps(t)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
