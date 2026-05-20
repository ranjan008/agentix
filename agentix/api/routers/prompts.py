"""
Prompts router — versioned system prompt management.

GET    /prompts                   — list all prompt names
GET    /prompts/{name}            — get latest active version of a prompt
GET    /prompts/{name}/versions   — list all versions of a prompt
GET    /prompts/{name}/{version}  — get a specific version
POST   /prompts                   — create a new prompt version (draft)
POST   /prompts/{id}/publish      — publish a draft → active
POST   /prompts/{id}/archive      — archive an active prompt
PATCH  /prompts/{id}              — update draft content
DELETE /prompts/{id}              — delete a draft
POST   /prompts/{name}/render     — render a prompt with variables
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_current_identity
from agentix.prompts.store import PromptStore

router = APIRouter()


def _store() -> PromptStore:
    return PromptStore(os.environ.get("AGENTIX_DB_PATH", "data/agentix.db"))


class CreatePromptBody(BaseModel):
    name: str
    version: str
    content: str
    variables: list[str] | None = None
    description: str | None = None
    author: str | None = None
    tags: dict | None = None
    publish: bool = False


class UpdatePromptBody(BaseModel):
    content: str
    variables: list[str] | None = None


class RenderBody(BaseModel):
    values: dict[str, str]
    version: str | None = None


@router.get("/prompts")
async def list_prompts(
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    names = _store().list_names(tenant_id=tenant_id)
    return {"names": names}


@router.get("/prompts/{name}")
async def get_prompt(
    name: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    p = _store().get_latest(name, tenant_id=tenant_id)
    if not p:
        raise HTTPException(status_code=404, detail="No active prompt found with this name")
    return p.to_dict()


@router.get("/prompts/{name}/versions")
async def list_versions(
    name: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    versions = _store().list_versions(name=name, tenant_id=tenant_id)
    return {"name": name, "versions": [v.to_dict() for v in versions]}


@router.get("/prompts/{name}/{version}")
async def get_prompt_version(
    name: str,
    version: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    p = _store().get_version(name, version, tenant_id=tenant_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prompt version not found")
    return p.to_dict()


@router.post("/prompts", status_code=201)
async def create_prompt(
    body: CreatePromptBody,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    store = _store()
    try:
        p = store.create(
            name=body.name,
            version=body.version,
            content=body.content,
            variables=body.variables,
            description=body.description,
            author=body.author,
            tenant_id=tenant_id,
            tags=body.tags,
            status="draft",
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper():
            raise HTTPException(status_code=409, detail=f"Prompt {body.name}@{body.version} already exists")
        raise
    if body.publish:
        store.publish(p.id)
        published = store.get_by_id(p.id)
        if published is None:
            raise HTTPException(status_code=500, detail="Failed to fetch published prompt")
        p = published
    return p.to_dict()


@router.post("/prompts/{prompt_id}/publish", status_code=200)
async def publish_prompt(
    prompt_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    store = _store()
    if not store.get_by_id(prompt_id):
        raise HTTPException(status_code=404, detail="Prompt not found")
    store.publish(prompt_id)
    return {"id": prompt_id, "status": "active"}


@router.post("/prompts/{prompt_id}/archive", status_code=200)
async def archive_prompt(
    prompt_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    store = _store()
    if not store.get_by_id(prompt_id):
        raise HTTPException(status_code=404, detail="Prompt not found")
    store.archive(prompt_id)
    return {"id": prompt_id, "status": "archived"}


@router.patch("/prompts/{prompt_id}")
async def update_prompt(
    prompt_id: str,
    body: UpdatePromptBody,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    store = _store()
    p = store.get_by_id(prompt_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    if p.status != "draft":
        raise HTTPException(status_code=422, detail="Only draft prompts can be updated")
    store.update_content(prompt_id, body.content, body.variables)
    return store.get_by_id(prompt_id).to_dict()  # type: ignore[union-attr]


@router.delete("/prompts/{prompt_id}", status_code=204)
async def delete_prompt(
    prompt_id: str,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> None:
    store = _store()
    p = store.get_by_id(prompt_id)
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    if p.status != "draft":
        raise HTTPException(status_code=422, detail="Only draft prompts can be deleted")
    store.delete(prompt_id)


@router.post("/prompts/{name}/render")
async def render_prompt(
    name: str,
    body: RenderBody,
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    tenant_id = identity.get("tenant_id", "default")
    store = _store()
    p = (
        store.get_version(name, body.version, tenant_id=tenant_id)
        if body.version
        else store.get_latest(name, tenant_id=tenant_id)
    )
    if not p:
        raise HTTPException(status_code=404, detail="Prompt not found")
    try:
        rendered = p.render(body.values)
    except KeyError as e:
        raise HTTPException(status_code=422, detail=f"Missing variable: {e}")
    return {"name": name, "version": p.version, "rendered": rendered}
