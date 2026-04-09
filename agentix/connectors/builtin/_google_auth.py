"""
Shared Google auth helper for all Google Workspace connectors.

Supports two auth modes:
  1. access_token   — a direct OAuth2 bearer token
  2. credentials_json — a service account key JSON string.
                        Requires `google-auth` package:
                        pip install google-auth

Both modes are set in the connector config dict.
"""
from __future__ import annotations

import json
import logging

import httpx

_logger = logging.getLogger(__name__)


def _build_client(cfg: dict, scopes: list[str]) -> httpx.AsyncClient:
    """
    Return an httpx.AsyncClient pre-configured with a valid Bearer token.

    Priority:
      1. cfg["access_token"]  — used as-is
      2. cfg["credentials_json"] — service account → exchange for token
    """
    token = cfg.get("access_token", "").strip()

    if not token:
        raw = cfg.get("credentials_json", "")
        if not raw:
            raise ValueError(
                "Google connector requires either 'access_token' or 'credentials_json' in config"
            )
        token = _service_account_token(raw, scopes)

    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=30,
    )


def _service_account_token(credentials_json: str | dict, scopes: list[str]) -> str:
    """
    Exchange a service account key for a short-lived access token.
    Requires the `google-auth` package.
    """
    try:
        from google.oauth2 import service_account  # type: ignore
        import google.auth.transport.requests as ga_requests  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Install 'google-auth' to use service account credentials: "
            "pip install google-auth"
        ) from exc

    if isinstance(credentials_json, str):
        info = json.loads(credentials_json)
    else:
        info = credentials_json

    creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    creds.refresh(ga_requests.Request())
    return creds.token
