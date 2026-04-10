"""
Chat router — UI chat window sends messages to an agent and polls for response.

POST /chat/send        — fire a trigger, return trigger_id immediately
GET  /chat/{trigger_id} — poll for response (returns status + response text)
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_store, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    agent_id: str = "telegram-agent"


class ChatResponse(BaseModel):
    trigger_id: str
    status: str
    response: str | None = None


@router.post("/chat/send", response_model=ChatResponse)
async def chat_send(
    body: ChatRequest,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> ChatResponse:
    """Create a trigger for the given agent and return the trigger_id for polling."""
    from datetime import datetime, timezone

    trigger_id = f"trig_{uuid.uuid4().hex[:16]}"
    envelope = {
        "id": trigger_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "channel": "http_webhook",
        "channel_meta": {"source": "ui_chat"},
        "caller": {
            "identity_id": identity.get("identity_id", "anonymous"),
            "roles": identity.get("roles", ["end-user"]),
            "tenant_id": identity.get("tenant_id", "default"),
        },
        "payload": {
            "text": body.message,
            "attachments": [],
            "context": {},
        },
        "agent_id": body.agent_id,
        "priority": "normal",
        "idempotency_key": trigger_id,
    }

    # Persist trigger so the watchdog can pick it up (if running)
    # and so /chat/{trigger_id} can poll for the result
    store.create_trigger(envelope)

    # Fire via the watchdog HTTP endpoint (same server, port 8080)
    import httpx
    import os
    watchdog_url = os.environ.get("WATCHDOG_URL", "http://localhost:8080/trigger")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(watchdog_url, json={
                "agent_id": body.agent_id,
                "text": body.message,
                "trigger_id": trigger_id,
            })
    except Exception:
        pass  # Watchdog may not be running; trigger is still persisted

    return ChatResponse(trigger_id=trigger_id, status="queued")


@router.get("/chat/{trigger_id}", response_model=ChatResponse)
async def chat_poll(
    trigger_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> ChatResponse:
    """Poll for agent response. Returns status + response when done."""
    trigger = store.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    return ChatResponse(
        trigger_id=trigger_id,
        status=trigger.get("status", "queued"),
        response=trigger.get("response"),
    )
