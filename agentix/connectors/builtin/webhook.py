"""Generic HTTP webhook connector — call any URL."""
from __future__ import annotations
import json
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("call", "Make an HTTP request to the configured webhook URL",
        {"type": "object",
         "properties": {
             "method": {"type": "string", "enum": ["GET","POST","PUT","PATCH","DELETE"], "default": "POST"},
             "path": {"type": "string", "description": "Optional path appended to base URL"},
             "body": {"type": "object", "description": "Request body (JSON)"},
             "query": {"type": "object", "description": "Query parameters"},
             "headers": {"type": "object", "description": "Additional headers"},
         }, "required": []}),
]


@register_connector("webhook")
class WebhookConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="webhook", display_name="Custom Webhook",
        description="Call any HTTP/HTTPS endpoint with custom headers, method, and body.",
        category="custom", icon="🔗", auth_type="none",
        required_config=["url"],
        optional_config=["method", "headers", "auth_header", "verify_ssl"],
        actions=_ACTIONS,
    )

    def _client(self) -> httpx.AsyncClient:
        headers: dict = {"Content-Type": "application/json"}
        configured_headers = self._cfg.get("headers", {})
        if isinstance(configured_headers, str):
            try:
                configured_headers = json.loads(configured_headers)
            except Exception:
                configured_headers = {}
        headers.update(configured_headers)

        auth_header = self._cfg.get("auth_header", "")
        if auth_header:
            headers["Authorization"] = auth_header

        verify = self._cfg.get("verify_ssl", True)
        return httpx.AsyncClient(headers=headers, timeout=30, verify=bool(verify))

    async def connect(self) -> None:
        self._require("url")  # just validate URL is set

    async def call(self, method: str = "POST", path: str = "", body: dict | None = None,
                   query: dict | None = None, headers: dict | None = None) -> dict:
        url = self._require("url").rstrip("/")
        if path:
            url = f"{url}/{path.lstrip('/')}"

        default_method = self._cfg.get("method", "POST").upper()
        final_method = method.upper() if method else default_method

        async with self._client() as c:
            extra_headers = headers or {}
            r = await c.request(
                method=final_method,
                url=url,
                json=body,
                params=query,
                headers=extra_headers,
            )
            try:
                resp_body = r.json()
            except Exception:
                resp_body = r.text
            return {"status_code": r.status_code, "ok": r.is_success, "body": resp_body}
