"""
Compliance API router.

GET  /compliance/oecd/export          — download OECD Due Diligence ZIP
GET  /compliance/soc2/export          — download SOC2 evidence ZIP
GET  /compliance/remediation          — list remediation items
POST /compliance/remediation          — open a new remediation item
PATCH /compliance/remediation/{id}    — update status / resolve
GET  /compliance/gdpr/export/{id}     — GDPR data portability export
DELETE /compliance/gdpr/{id}          — GDPR right to erasure
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from agentix.api.deps import get_store, require_admin
from agentix.storage.state_store import StateStore

router = APIRouter()


def _db_path() -> str:
    return os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")


def _hmac_secret() -> str:
    return os.environ.get("AUDIT_HMAC_SECRET", "")


def _cfg(store: StateStore) -> dict:
    return {}


# ---------------------------------------------------------------------------
# OECD Due Diligence export
# ---------------------------------------------------------------------------

@router.get("/compliance/oecd/export")
async def oecd_export(
    identity: Annotated[dict, Depends(require_admin)],
    period_days: int = Query(90, ge=1, le=365),
):
    """Generate and download an OECD Due Diligence evidence ZIP."""
    from agentix.compliance.oecd import OECDDueDiligenceReport

    db = _db_path()
    with tempfile.TemporaryDirectory() as tmp:
        report = OECDDueDiligenceReport(
            db_path=db,
            cfg={},
            hmac_secret=_hmac_secret(),
            period_days=period_days,
        )
        zip_path = report.export(output_dir=tmp)
        content = Path(zip_path).read_bytes()

    filename = Path(zip_path).name
    return _zip_response(content, filename)


# ---------------------------------------------------------------------------
# SOC2 evidence export
# ---------------------------------------------------------------------------

@router.get("/compliance/soc2/export")
async def soc2_export(
    identity: Annotated[dict, Depends(require_admin)],
):
    """Generate and download a SOC2 evidence ZIP."""
    from agentix.compliance.soc2 import SOC2Exporter

    db = _db_path()
    with tempfile.TemporaryDirectory() as tmp:
        exporter = SOC2Exporter(db_path=db, cfg={}, hmac_secret=_hmac_secret())
        zip_path = exporter.export(output_dir=tmp)
        content = Path(zip_path).read_bytes()

    filename = Path(zip_path).name
    return _zip_response(content, filename)


# ---------------------------------------------------------------------------
# Remediation log
# ---------------------------------------------------------------------------

class RemediationOpenBody(BaseModel):
    harm_type: str
    severity: str
    description: str
    owner: str | None = None
    audit_seq_ref: int | None = None
    metadata: dict | None = None
    tenant_id: str = "default"


class RemediationUpdateBody(BaseModel):
    status: str
    owner: str | None = None
    resolution_note: str | None = None
    tenant_id: str = "default"


@router.get("/compliance/remediation")
async def list_remediation(
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str | None = Query(None),
    severity: str | None = Query(None),
    include_resolved: bool = Query(False),
):
    """List remediation items."""
    from agentix.compliance.remediation import RemediationLog

    rlog = RemediationLog(db_path=_db_path())
    if include_resolved:
        # full summary covers both open and closed
        summary = rlog.summary(tenant_id=tenant_id)
        open_items = rlog.list_open(tenant_id=tenant_id, severity=severity)
        return {"items": open_items, "summary": summary}
    items = rlog.list_open(tenant_id=tenant_id, severity=severity)
    return {"items": items, "summary": rlog.summary(tenant_id=tenant_id)}


@router.post("/compliance/remediation", status_code=201)
async def open_remediation(
    body: RemediationOpenBody,
    identity: Annotated[dict, Depends(require_admin)],
):
    """Open a new remediation item."""
    from agentix.compliance.remediation import RemediationLog

    rlog = RemediationLog(db_path=_db_path())
    try:
        item_id = rlog.open_item(
            harm_type=body.harm_type,
            severity=body.severity,
            description=body.description,
            owner=body.owner,
            audit_seq_ref=body.audit_seq_ref,
            metadata=body.metadata,
            tenant_id=body.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = rlog.get(item_id)
    return item


@router.patch("/compliance/remediation/{item_id}")
async def update_remediation(
    item_id: int,
    body: RemediationUpdateBody,
    identity: Annotated[dict, Depends(require_admin)],
):
    """Update the status of a remediation item."""
    from agentix.compliance.remediation import RemediationLog

    rlog = RemediationLog(db_path=_db_path())
    if rlog.get(item_id) is None:
        raise HTTPException(status_code=404, detail="Remediation item not found")
    try:
        rlog.update_status(
            item_id=item_id,
            status=body.status,
            owner=body.owner,
            resolution_note=body.resolution_note,
            tenant_id=body.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return rlog.get(item_id)


# ---------------------------------------------------------------------------
# GDPR
# ---------------------------------------------------------------------------

@router.get("/compliance/gdpr/export/{identity_id}")
async def gdpr_export(
    identity_id: str,
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str = Query("default"),
):
    """GDPR Article 20 — data portability export for an identity."""
    from agentix.compliance.gdpr import GDPREngine

    engine = GDPREngine(db_path=_db_path())
    data = engine.data_export(identity_id=identity_id, tenant_id=tenant_id)
    return JSONResponse(content=data)


@router.delete("/compliance/gdpr/{identity_id}", status_code=200)
async def gdpr_erasure(
    identity_id: str,
    identity: Annotated[dict, Depends(require_admin)],
    tenant_id: str = Query("default"),
):
    """GDPR Article 17 — right to erasure."""
    from agentix.compliance.gdpr import GDPREngine

    engine = GDPREngine(db_path=_db_path())
    result = engine.right_to_erasure(identity_id=identity_id, tenant_id=tenant_id)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zip_response(content: bytes, filename: str):
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
