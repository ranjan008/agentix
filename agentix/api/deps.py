"""
FastAPI dependency injection helpers.

Provides:
  - get_store()  — yields a StateStore instance
  - require_admin()  — validates Bearer token and requires admin role
  - get_current_identity()  — resolves any valid identity
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agentix.storage.state_store import StateStore
from agentix.watchdog.auth import validate_jwt

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _get_store() -> StateStore:
    db_path = os.environ.get("AGENTIX_DB_PATH", "data/agentix.db")
    return StateStore(db_path)


def get_store() -> StateStore:
    return _get_store()


def _resolve_identity(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        return {"identity_id": "anonymous", "roles": ["end-user"], "tenant_id": "default"}

    token = credentials.credentials
    jwt_secret = os.environ.get("JWT_SECRET", "")

    # Try API key prefix
    if token.startswith("sk-agentix-"):
        from agentix.security.identity.provider import ServiceAccountManager
        sam = ServiceAccountManager(_get_store().db_path)
        identity = sam.validate(token)
        if identity:
            return {"identity_id": identity.identity_id, "roles": identity.roles, "tenant_id": identity.tenant_id}
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Try JWT
    if jwt_secret:
        claims = validate_jwt(token, jwt_secret)
        if claims:
            return {"identity_id": claims.get("sub", "unknown"), "roles": claims.get("roles", ["end-user"]), "tenant_id": claims.get("tenant_id", "default")}

    raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    return _resolve_identity(credentials)


def require_admin(
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    if "platform-admin" not in identity.get("roles", []) and "tenant-admin" not in identity.get("roles", []):
        raise HTTPException(status_code=403, detail="Admin role required")
    return identity


def require_platform_admin(
    identity: Annotated[dict, Depends(get_current_identity)],
) -> dict:
    if "platform-admin" not in identity.get("roles", []):
        raise HTTPException(status_code=403, detail="Platform admin role required")
    return identity
