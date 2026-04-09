"""
Connectors API router.

Endpoints:
  GET    /connectors          — list configured connectors (credentials masked)
  POST   /connectors          — create / configure a connector
  GET    /connectors/catalog  — public catalog of available connector types
  GET    /connectors/{name}   — get a single connector
  PUT    /connectors/{name}   — update config
  DELETE /connectors/{name}   — remove connector
  POST   /connectors/{name}/test    — test connection
  PATCH  /connectors/{name}/enable  — enable/disable
  GET    /connectors/{name}/tools   — list available tool schemas
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel

from agentix.api.deps import get_store, require_admin
from agentix.connectors.catalog import CATALOG, CATALOG_BY_TYPE
from agentix.connectors.registry import get_connector_class

_logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connectors", tags=["connectors"])

# ── Request / response models ───────────────────────────────────────────────

class ConnectorCreate(BaseModel):
    name: str
    type: str
    config: dict[str, Any] = {}
    enabled: bool = True


class ConnectorUpdate(BaseModel):
    config: dict[str, Any] | None = None
    enabled: bool | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

_MASK_KEYS = {"token", "api_key", "api_token", "password", "secret", "auth_token",
              "bot_token", "client_secret", "credentials_json", "auth_header"}


def _mask(config: dict) -> dict:
    """Return a copy of config with sensitive fields redacted."""
    return {
        k: ("***" if k in _MASK_KEYS else v)
        for k, v in config.items()
    }


def _enrich(record: dict) -> dict:
    """Add catalog metadata to a stored connector record."""
    meta = CATALOG_BY_TYPE.get(record["type"], {})
    return {
        **record,
        "config": _mask(record.get("config", {})),
        "display_name": meta.get("display_name", record["type"]),
        "icon": meta.get("icon", "🔌"),
        "category": meta.get("category", "custom"),
        "description": meta.get("description", ""),
        "available_actions": meta.get("actions", []),
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("")
def list_connectors(store=Depends(get_store), _=Depends(require_admin)):
    """List all configured connectors with credentials masked."""
    records = store.list_connectors()
    return {"connectors": [_enrich(r) for r in records]}


@router.get("/catalog")
def get_catalog():
    """Return the full public catalog of available connector types."""
    return {"catalog": CATALOG, "total": len(CATALOG)}


@router.post("", status_code=201)
def create_connector(
    body: ConnectorCreate,
    store=Depends(get_store),
    _=Depends(require_admin),
):
    """Configure a new connector. Fails if type is not in catalog."""
    if body.type not in CATALOG_BY_TYPE:
        raise HTTPException(400, f"Unknown connector type '{body.type}'. "
                            f"Available: {list(CATALOG_BY_TYPE)}")
    existing = store.get_connector(body.name)
    if existing:
        raise HTTPException(409, f"Connector '{body.name}' already exists. Use PUT to update.")

    store.upsert_connector(body.name, body.type, body.config)
    return {"name": body.name, "type": body.type, "status": "pending"}


@router.get("/{name}")
def get_connector(name: str, store=Depends(get_store), _=Depends(require_admin)):
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")
    return _enrich(rec)


@router.put("/{name}")
def update_connector(
    name: str,
    body: ConnectorUpdate,
    store=Depends(get_store),
    _=Depends(require_admin),
):
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")

    merged_config = rec["config"]
    if body.config is not None:
        merged_config = {**merged_config, **body.config}

    store.upsert_connector(name, rec["type"], merged_config)

    if body.enabled is not None:
        store.set_connector_enabled(name, body.enabled)

    return {"name": name, "updated": True}


@router.delete("/{name}", status_code=204)
def delete_connector(name: str, store=Depends(get_store), _=Depends(require_admin)):
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")
    store.delete_connector(name)


@router.post("/{name}/test")
async def test_connector(name: str, store=Depends(get_store), _=Depends(require_admin)):
    """Instantiate the connector and call connect() to validate credentials."""
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")

    cls = get_connector_class(rec["type"])
    if cls is None:
        raise HTTPException(422, f"Connector type '{rec['type']}' has no implementation")

    instance = cls(rec["config"])
    result = await instance.test()

    status = "connected" if result["ok"] else "error"
    store.update_connector_status(name, status, result.get("error"))

    return {"name": name, **result}


@router.patch("/{name}/enable")
def toggle_connector(
    name: str,
    enabled: bool = Body(..., embed=True),
    store=Depends(get_store),
    _=Depends(require_admin),
):
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")
    store.set_connector_enabled(name, enabled)
    return {"name": name, "enabled": enabled}


@router.get("/{name}/tools")
def get_connector_tools(name: str, store=Depends(get_store), _=Depends(require_admin)):
    """Return the Anthropic-compatible tool schemas for a connector type."""
    rec = store.get_connector(name)
    if not rec:
        raise HTTPException(404, f"Connector '{name}' not found")
    cls = get_connector_class(rec["type"])
    if cls is None:
        raise HTTPException(422, f"No implementation for type '{rec['type']}'")
    instance = cls(rec["config"])
    return {"name": name, "tools": instance.tool_schemas()}
