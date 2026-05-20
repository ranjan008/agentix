"""
Traces router — LangSmith-lite observability API.

GET  /traces                     — list recent traces (filterable by agent, status)
GET  /traces/{trace_id}          — get a single trace with its full span tree
GET  /traces/{trace_id}/spans    — get spans for a trace
GET  /triggers/{trigger_id}/trace — get the trace for a specific trigger run
DELETE /traces/{trace_id}        — delete a trace and its spans
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from agentix.api.deps import get_current_identity
from agentix.observability.trace_store import TraceStore

router = APIRouter()

_PROJECT_ROOT = str(Path(__file__).resolve().parents[3])


def _trace_store() -> TraceStore:
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    if not os.path.isabs(db_path):
        db_path = str(Path(_PROJECT_ROOT) / db_path)
    return TraceStore(db_path)


@router.get("/traces")
async def list_traces(
    identity: Annotated[dict, Depends(get_current_identity)],
    agent_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    # Treat the literal string "undefined" (sent by older UI builds) as no filter.
    if agent_id in (None, "", "undefined"):
        agent_id = None
    if status in (None, "", "undefined"):
        status = None

    # Platform-admins see all traces; tenant-admins/users see their tenant only.
    roles = identity.get("roles", [])
    tenant_id = None if "platform-admin" in roles else identity.get("tenant_id", "default")

    store = _trace_store()
    traces = store.list_traces(
        agent_id=agent_id,
        tenant_id=tenant_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    total = store.count_traces(agent_id=agent_id, tenant_id=tenant_id, status=status)
    return {"traces": traces, "total": total, "limit": limit, "offset": offset}


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
    include_spans: bool = Query(default=True),
) -> dict:
    store = _trace_store()
    if include_spans:
        trace = store.get_trace_with_spans(trace_id)
    else:
        trace = store.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace


@router.get("/traces/{trace_id}/spans")
async def get_trace_spans(
    trace_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    store = _trace_store()
    if not store.get_trace(trace_id):
        raise HTTPException(status_code=404, detail="Trace not found")
    spans = store.get_spans(trace_id)
    return {"trace_id": trace_id, "spans": spans}


@router.get("/triggers/{trigger_id}/trace")
async def get_trigger_trace(
    trigger_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    trace = _trace_store().get_trace_by_trigger(trigger_id)
    if not trace:
        raise HTTPException(status_code=404, detail="No trace found for this trigger")
    return trace


@router.delete("/traces/{trace_id}", status_code=204)
async def delete_trace(
    trace_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> None:
    store = _trace_store()
    if not store.get_trace(trace_id):
        raise HTTPException(status_code=404, detail="Trace not found")
    store.delete_trace(trace_id)
