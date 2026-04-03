"""
Tenants router.

GET    /tenants              — list tenants
POST   /tenants              — create tenant
GET    /tenants/{tenant_id}  — tenant detail
DELETE /tenants/{tenant_id}  — delete tenant (soft)
POST   /tenants/{tenant_id}/service-accounts — create service account API key
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_store, require_platform_admin, require_admin
from agentix.storage.state_store import StateStore

router = APIRouter()


class TenantCreate(BaseModel):
    tenant_id: str
    name: str
    tier: str = "standard"    # lite | standard | enterprise
    metadata: dict = {}


class ServiceAccountCreate(BaseModel):
    name: str
    roles: list[str] = ["operator"]
    scopes: list[str] = []
    ttl_days: int = 365


@router.get("/tenants")
async def list_tenants(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_platform_admin)],
) -> list[dict]:
    return store.list_tenants()


@router.post("/tenants", status_code=201)
async def create_tenant(
    body: TenantCreate,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_platform_admin)],
) -> dict:
    from agentix.storage.tenant import apply_tenant_migration
    import os
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    apply_tenant_migration(db_path)
    store.upsert_tenant(body.tenant_id, body.name, body.tier, body.metadata)
    return {"tenant_id": body.tenant_id, "status": "created"}


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    tenant = store.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return tenant


@router.delete("/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: str,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_platform_admin)],
) -> None:
    store.soft_delete_tenant(tenant_id)


@router.post("/tenants/{tenant_id}/service-accounts", status_code=201)
async def create_service_account(
    tenant_id: str,
    body: ServiceAccountCreate,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    import os
    from agentix.security.identity.provider import ServiceAccountManager
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    sam = ServiceAccountManager(db_path)
    sa_id, plain_key = sam.create(
        name=body.name,
        roles=body.roles,
        tenant_id=tenant_id,
        scopes=body.scopes,
        ttl_days=body.ttl_days,
    )
    # Return plain key ONCE — not stored in plaintext
    return {
        "service_account_id": sa_id,
        "name": body.name,
        "api_key": plain_key,
        "warning": "Store this API key securely — it will not be shown again.",
    }
