"""
Skills management router.

GET    /skills              — list available skills (bundled + installed)
GET    /skills/{name}       — skill detail
POST   /skills/{name}/install — install from marketplace
DELETE /skills/{name}       — uninstall
GET    /skills/marketplace  — search marketplace catalog
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from agentix.api.deps import get_store, require_admin, get_current_identity
from agentix.storage.state_store import StateStore

router = APIRouter()


class InstallRequest(BaseModel):
    version: str | None = None
    verify_signature: bool = True


@router.get("/skills")
async def list_skills(
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(get_current_identity)],
) -> list[dict]:
    try:
        from agentix.skills.marketplace import SkillMarketplace
        mp = SkillMarketplace()
        return [{"name": s["name"], "description": s.get("description", ""), "version": s.get("version", ""), "source": "marketplace"} for s in mp.list_all()]
    except Exception:
        return []


@router.get("/skills/marketplace")
async def search_marketplace(
    q: str = Query(""),
    identity: Annotated[Optional[dict], Depends(get_current_identity)] = None,
) -> list[dict]:
    from agentix.skills.marketplace import SkillMarketplace
    mp = SkillMarketplace()
    results = mp.search(q)
    return [{"name": r["name"], "description": r.get("description", ""), "version": r.get("version", ""), "tags": r.get("tags", [])} for r in results]


@router.post("/skills/{name}/install", status_code=201)
async def install_skill(
    name: str,
    body: InstallRequest,
    store: Annotated[StateStore, Depends(get_store)],
    identity: Annotated[dict, Depends(require_admin)],
) -> dict:
    from agentix.skills.marketplace import SkillMarketplace
    from agentix.skills.skillhub import SkillHub
    mp = SkillMarketplace()
    hub = SkillHub(store)
    record = mp.install(name, skillhub=hub)
    return {"name": name, "status": "installed", "path": str(record.get("install_path", ""))}


@router.delete("/skills/{name}", status_code=204)
async def uninstall_skill(
    name: str,
    identity: Annotated[dict, Depends(require_admin)],
) -> None:
    import shutil
    from pathlib import Path
    skill_path = Path("skills") / name
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    shutil.rmtree(skill_path)
