"""
HITL router — human-in-the-loop interrupt / resume.

GET  /triggers/{id}/checkpoint  — inspect a paused trigger's state
POST /triggers/{id}/resume       — approve | reject | edit a pending tool call
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_store, get_current_identity
from agentix.hitl.checkpoint import CheckpointStore
from agentix.storage.state_store import StateStore

router = APIRouter()

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])


def _cp_store() -> CheckpointStore:
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    if not os.path.isabs(db_path):
        db_path = str(Path(_PROJECT_ROOT) / db_path)
    return CheckpointStore(db_path)


class ResumeRequest(BaseModel):
    action: Literal["approve", "reject", "edit"]
    edit: dict | None = None  # overrides for pending tool call inputs


@router.get("/triggers/{trigger_id}/checkpoint")
async def get_checkpoint(
    trigger_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    cp = _cp_store().load(trigger_id)
    if not cp:
        raise HTTPException(status_code=404, detail="No pending checkpoint for this trigger")
    return cp.to_dict()


@router.post("/triggers/{trigger_id}/resume", status_code=202)
async def resume_trigger(
    trigger_id: str,
    body: ResumeRequest,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    cp_store = _cp_store()
    cp = cp_store.load(trigger_id)
    if not cp:
        raise HTTPException(status_code=404, detail="No pending checkpoint for this trigger")

    trigger = store.get_trigger(trigger_id)
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    if body.action == "reject":
        # Drop pending calls, delete checkpoint, mark done with rejection note
        cp_store.delete(trigger_id)
        store.update_trigger_status(trigger_id, "done")
        store.save_trigger_response(trigger_id, "[HITL] Pending tool calls rejected by operator.")
        return {"trigger_id": trigger_id, "action": "reject", "status": "done"}

    if body.action == "edit" and body.edit:
        # Apply edits to the first pending tool call's input
        for pending in cp.pending_calls:
            pending["input"].update(body.edit)

    # approve or edited — re-spawn the agent with the checkpoint in place (it will auto-load)
    store.update_trigger_status(trigger_id, "running")

    try:
        envelope = json.loads(trigger.get("payload", "{}"))
    except Exception:
        envelope = {}

    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    if not os.path.isabs(db_path):
        db_path = str(Path(_PROJECT_ROOT) / db_path)

    def _respawn(env_dict: dict, db: str) -> None:
        env = dict(os.environ)
        try:
            from dotenv import dotenv_values
            for k, v in dotenv_values().items():
                if v is not None:
                    env.setdefault(k, v)
        except Exception:
            pass
        env["AGENTIX_TRIGGER"] = json.dumps(env_dict)
        env["AGENTIX_DB_PATH"] = db
        subprocess.run(
            [sys.executable, "-m", "agentix.agent_runtime.main"],
            env=env, capture_output=True, timeout=300, cwd=_PROJECT_ROOT,
        )

    t = threading.Thread(target=_respawn, args=(envelope, db_path), daemon=True,
                         name=f"hitl-resume-{trigger_id}")
    t.start()

    return {"trigger_id": trigger_id, "action": body.action, "status": "running"}
