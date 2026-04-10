"""
Auth router — login, token validation, and user profile.

Endpoints:
  POST /auth/login        — local admin email/password → JWT
  POST /auth/token        — exchange Auth0 access_token → enriched JWT
  GET  /auth/me           — return current identity from Bearer token
  GET  /auth/config       — return Auth0 domain/clientId for the UI (public)
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agentix.api.deps import get_current_identity
from agentix.watchdog.auth import make_jwt

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str
    password: str


class TokenExchangeRequest(BaseModel):
    access_token: str          # Auth0-issued access token


class LoginResponse(BaseModel):
    token: str
    identity_id: str
    email: str
    roles: list[str]
    tenant_id: str


# ---------------------------------------------------------------------------
# Public: return Auth0 config so the UI can initialise the SDK
# ---------------------------------------------------------------------------

@router.get("/auth/config")
async def auth_config() -> dict:
    """Return public Auth0 configuration for the frontend SDK."""
    return {
        "auth0_domain": os.environ.get("AUTH0_DOMAIN", ""),
        "auth0_client_id": os.environ.get("AUTH0_CLIENT_ID", ""),
        "auth0_audience": os.environ.get("AUTH0_AUDIENCE", ""),
        "local_auth_enabled": bool(os.environ.get("ADMIN_EMAIL")),
    }


# ---------------------------------------------------------------------------
# Local admin login (email + password from .env)
# ---------------------------------------------------------------------------

@router.post("/auth/login", response_model=LoginResponse)
async def local_login(body: LoginRequest) -> LoginResponse:
    """
    Authenticate with admin credentials defined in .env:
      ADMIN_EMAIL=admin@example.com
      ADMIN_PASSWORD=changeme
    Issues a local HS256 JWT with platform-admin role.
    """
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_email or not admin_password:
        raise HTTPException(status_code=404, detail="Local login not configured")

    if body.email != admin_email or body.password != admin_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET not configured")

    token = make_jwt(
        claims={"sub": admin_email, "email": admin_email, "name": "Admin",
                "roles": ["platform-admin"], "tenant_id": "default"},
        secret=jwt_secret,
        ttl_sec=86400,
    )

    return LoginResponse(
        token=token,
        identity_id=admin_email,
        email=admin_email,
        roles=["platform-admin"],
        tenant_id="default",
    )


# ---------------------------------------------------------------------------
# Auth0 token exchange — validate Auth0 JWT, extract roles, issue local JWT
# ---------------------------------------------------------------------------

@router.post("/auth/token", response_model=LoginResponse)
async def exchange_auth0_token(body: TokenExchangeRequest) -> LoginResponse:
    """
    Validate an Auth0 access token, extract roles from the custom claim
    (https://agentix/roles), and issue a local JWT for the API.
    """
    domain = os.environ.get("AUTH0_DOMAIN", "")
    audience = os.environ.get("AUTH0_AUDIENCE", "")

    if not domain:
        raise HTTPException(status_code=400, detail="Auth0 not configured on this server")

    import jwt as pyjwt
    from jwt import PyJWKClient

    try:
        jwks_url = f"https://{domain}/.well-known/jwks.json"
        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(body.access_token)
        payload = pyjwt.decode(
            body.access_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
        )
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Invalid Auth0 token: {exc}")

    # Extract roles from Auth0 custom claim — set this up in Auth0 Actions:
    # event.user.app_metadata.roles → custom claim "https://agentix/roles"
    roles: list[str] = payload.get("https://agentix/roles", ["end-user"])
    email: str = payload.get("email", payload.get("sub", "unknown"))
    name: str = payload.get("name", email)
    tenant_id: str = payload.get("https://agentix/tenant_id", "default")

    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET not configured")

    token = make_jwt(
        claims={"sub": email, "email": email, "name": name,
                "roles": roles, "tenant_id": tenant_id},
        secret=jwt_secret,
        ttl_sec=3600,
    )

    return LoginResponse(
        token=token,
        identity_id=email,
        email=email,
        roles=roles,
        tenant_id=tenant_id,
    )


# ---------------------------------------------------------------------------
# /auth/me — return current identity
# ---------------------------------------------------------------------------

@router.get("/auth/me")
async def me(identity: Annotated[dict, Depends(get_current_identity)]) -> dict:
    return identity
