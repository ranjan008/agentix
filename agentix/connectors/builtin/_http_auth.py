"""Shared auth handling for the generic HTTP connectors (http_api, openapi).

One `auth_type` config key selects the scheme; the rest of the fields are
read only if that scheme needs them. Kept separate from either connector so
they can't drift on what "bearer" means.
"""
from __future__ import annotations


def apply_auth(cfg: dict, headers: dict, params: dict) -> tuple[str, str] | None:
    """Mutates `headers` / `params` in place per `cfg["auth_type"]`.

    Returns an (username, password) tuple when the scheme is HTTP Basic
    (the caller passes it to httpx as `auth=`), else None.

      none            (default) — nothing
      bearer          auth_token                  -> Authorization: Bearer <token>
      api_key_header  api_key, api_key_header_name (default X-API-Key)
      api_key_query   api_key, api_key_query_name  (default api_key)
      basic           username, password          -> returned for httpx auth=
    """
    auth_type = (cfg.get("auth_type") or "none").strip().lower()

    if auth_type in ("", "none"):
        return None

    if auth_type == "bearer":
        token = cfg.get("auth_token") or cfg.get("api_key") or ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return None

    if auth_type == "api_key_header":
        key = cfg.get("api_key") or ""
        name = cfg.get("api_key_header_name") or "X-API-Key"
        if key:
            headers[name] = key
        return None

    if auth_type == "api_key_query":
        key = cfg.get("api_key") or ""
        name = cfg.get("api_key_query_name") or "api_key"
        if key:
            params[name] = key
        return None

    if auth_type == "basic":
        return (cfg.get("username") or "", cfg.get("password") or "")

    raise ValueError(
        f"unknown auth_type {auth_type!r} — use one of: none, bearer, "
        "api_key_header, api_key_query, basic"
    )
