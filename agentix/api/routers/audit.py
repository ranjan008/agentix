"""
Audit log router.

GET  /audit              — paginated audit entries
GET  /audit/verify       — verify HMAC chain integrity
GET  /audit/export       — download audit log as NDJSON (SOC2 export)
"""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from agentix.api.deps import get_store, require_admin, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


@router.get("/audit")
async def list_audit(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    entries = store.list_audit(tenant_id=tenant_id, action=action, limit=limit, offset=offset)
    return {"entries": entries, "limit": limit, "offset": offset}


@router.get("/audit/verify")
async def verify_audit_chain(
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    import os
    from agentix.security.audit import AuditLog
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    hmac_secret = os.environ.get("AUDIT_HMAC_SECRET", "")
    audit = AuditLog(db_path=db_path, hmac_secret=hmac_secret)
    ok, tampered_ids = audit.verify_chain()
    return {
        "chain_valid": ok,
        "tampered_entry_ids": tampered_ids,
        "message": "Chain intact" if ok else f"{len(tampered_ids)} tampered entries detected",
    }


@router.get("/audit/export")
async def export_audit(
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str | None = Query(None),
):
    """Stream audit log as NDJSON for SOC2 export."""
    store = get_store()

    async def ndjson_stream():
        offset = 0
        batch = 500
        while True:
            entries = store.list_audit(tenant_id=tenant_id, limit=batch, offset=offset)
            if not entries:
                break
            for entry in entries:
                yield json.dumps(entry) + "\n"
            offset += batch
            if len(entries) < batch:
                break

    return StreamingResponse(
        ndjson_stream(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": "attachment; filename=audit-export.ndjson"},
    )
