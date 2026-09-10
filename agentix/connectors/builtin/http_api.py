"""Generic HTTP API connector — point an agent at any REST API.

One `request` tool: base URL + method/path/query/headers/body. The model
composes each call from the agent's system prompt. Distinct from `webhook`
(which is "fire a payload at a URL" with a raw Authorization paste): this
one has real auth schemes (bearer / API key in header or query / basic),
returns response headers, and can be locked to a method set
(`allowed_methods: "GET"` = a read-only connector).
"""
from __future__ import annotations

import json

import httpx

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.builtin._http_auth import apply_auth
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction(
        "request",
        "Make an HTTP request to the configured API. `path` is appended to the connector's base URL.",
        {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
                "path": {"type": "string", "description": "Path appended to the base URL, e.g. /v1/users/42"},
                "query": {"type": "object", "description": "Query-string parameters"},
                "headers": {"type": "object", "description": "Extra request headers"},
                "json_body": {"type": "object", "description": "JSON request body (for POST/PUT/PATCH)"},
            },
            "required": [],
        },
    ),
]

_VALID_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _as_dict(v) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


@register_connector("http_api")
class HttpApiConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="http_api",
        display_name="HTTP API",
        description="Call any REST API with proper auth (bearer, API key, or basic).",
        category="custom",
        icon="🌐",
        auth_type="api_key",
        required_config=["base_url"],
        optional_config=[
            "auth_type", "auth_token", "api_key", "api_key_header_name",
            "api_key_query_name", "username", "password", "default_headers",
            "verify_ssl", "allowed_methods",
        ],
        actions=_ACTIONS,
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._transport: httpx.BaseTransport | None = None  # test-injection hook only

    async def connect(self) -> None:
        base = self._require("base_url")
        if not base.lower().startswith(("http://", "https://")):
            raise ValueError(f"base_url must be an http(s) URL, got {base!r}")

    def _allowed_methods(self) -> set[str]:
        raw = self._cfg.get("allowed_methods")
        if not raw:
            return set(_VALID_METHODS)
        if isinstance(raw, str):
            raw = [m.strip() for m in raw.replace(",", " ").split()]
        return {m.upper() for m in raw if m} or set(_VALID_METHODS)

    async def request(
        self,
        method: str = "GET",
        path: str = "",
        query: dict | None = None,
        headers: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        m = (method or "GET").upper()
        if m not in _VALID_METHODS:
            raise ValueError(f"unsupported method {method!r}")
        allowed = self._allowed_methods()
        if m not in allowed:
            raise ValueError(f"this connector only permits {sorted(allowed)} requests, not {m}")

        url = self._require("base_url").rstrip("/")
        if path:
            url = f"{url}/{str(path).lstrip('/')}"

        req_headers = {"Accept": "application/json"}
        req_headers.update(_as_dict(self._cfg.get("default_headers")))
        req_headers.update(headers or {})
        params = dict(query or {})
        basic = apply_auth(self._cfg, req_headers, params)

        verify = self._cfg.get("verify_ssl", True)
        async with httpx.AsyncClient(
            timeout=30, verify=bool(verify) if not isinstance(verify, str) else verify.lower() != "false",
            transport=self._transport,
        ) as c:
            r = await c.request(
                m, url,
                params=params or None,
                headers=req_headers,
                json=json_body if json_body is not None else None,
                auth=basic,
            )
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {
            "status_code": r.status_code,
            "ok": r.is_success,
            "body": body,
            "headers": dict(r.headers),
        }
