"""
Chat router — UI chat window sends messages to an agent and polls for response.

POST /chat/send        — fire a trigger, return trigger_id immediately
GET  /chat/{trigger_id} — poll for response (returns status + response text)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_store, get_current_identity
from agentix.storage.state_store import StateStore

# Project root = three levels up from this file (agentix/api/routers/chat.py)
_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    agent_id: str


class ChatResponse(BaseModel):
    trigger_id: str
    status: str
    response: str | None = None
    error: str | None = None


def _run_agent_subprocess(envelope: dict, db_path: str) -> None:
    """Blocking subprocess — runs in a daemon thread."""
    agent_id = envelope["agent_id"]
    trigger_id = envelope["id"]

    env = dict(os.environ)
    try:
        from dotenv import dotenv_values
        for k, v in dotenv_values().items():
            if v is not None:
                env.setdefault(k, v)
    except Exception:
        pass
    env["AGENTIX_TRIGGER"] = json.dumps(envelope)
    env["AGENTIX_DB_PATH"] = db_path

    print(f"[agentix.chat] Spawning agent subprocess: agent={agent_id} trigger={trigger_id} cwd={_PROJECT_ROOT}", flush=True)
    logger.info("Spawning agent: agent=%s trigger=%s cwd=%s", agent_id, trigger_id, _PROJECT_ROOT)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "agentix.agent_runtime.main"],
            env=env,
            capture_output=True,
            timeout=300,
            cwd=_PROJECT_ROOT,
        )
        stderr = result.stderr.decode(errors="replace").strip()
        stdout = result.stdout.decode(errors="replace").strip()
        if result.returncode == 0:
            print(f"[agentix.chat] Agent completed: agent={agent_id} trigger={trigger_id}", flush=True)
            logger.info("Agent completed: agent=%s trigger=%s", agent_id, trigger_id)
            if stderr:
                logger.info("Agent stderr:\n%s", stderr[-3000:])
        else:
            msg = (
                f"[agentix.chat] Agent FAILED (rc={result.returncode}): "
                f"agent={agent_id} trigger={trigger_id}\n"
                f"STDERR: {stderr[-3000:]}\nSTDOUT: {stdout[-500:]}"
            )
            print(msg, flush=True)
            logger.error(
                "Agent failed (rc=%d): agent=%s trigger=%s\nSTDERR: %s\nSTDOUT: %s",
                result.returncode, agent_id, trigger_id,
                stderr[-3000:], stdout[-500:],
            )
    except subprocess.TimeoutExpired:
        print(f"[agentix.chat] Agent timed out: agent={agent_id} trigger={trigger_id}", flush=True)
        logger.error("Agent timed out: agent=%s trigger=%s", agent_id, trigger_id)
    except Exception as exc:
        print(f"[agentix.chat] Spawn error: agent={agent_id} trigger={trigger_id}: {exc}", flush=True)
        logger.exception("Spawn error: agent=%s trigger=%s: %s", agent_id, trigger_id, exc)


@router.post("/chat/send", response_model=ChatResponse)
async def chat_send(
    body: ChatRequest,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> ChatResponse:
    """Validate agent, persist trigger, spawn agent in background thread."""

    agent_spec = store.get_agent(body.agent_id)
    if not agent_spec:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{body.agent_id}' is not registered. "
                   f"Register it with: agentix agent register agents/{body.agent_id}.yaml",
        )

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

    store.create_trigger(envelope)

    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    # Ensure db_path is absolute so subprocess and API process use the same file
    if not os.path.isabs(db_path):
        db_path = str(Path(_PROJECT_ROOT) / db_path)
    t = threading.Thread(
        target=_run_agent_subprocess,
        args=(envelope, db_path),
        daemon=True,
        name=f"agent-{trigger_id}",
    )
    t.start()
    print(f"[agentix.chat] Thread started: agent={body.agent_id} trigger={trigger_id} db={db_path}", flush=True)
    logger.info("Agent thread started: agent=%s trigger=%s thread=%s", body.agent_id, trigger_id, t.name)

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
        error=trigger.get("error"),
    )
