"""OpenAPI / Swagger connector — point an agent at an API by its spec.

The tenant configures one `spec` (pasted JSON/YAML, or an http(s) URL to
one). At load time the spec is parsed into an operation map, and a single
`call_operation` tool is exposed whose description lists every operation
and its parameters. The model passes an `operation_id` plus args; the
connector validates it against the spec and builds the real request
(path-param substitution + the configured auth scheme).

One tool rather than one-per-operation (a deliberate choice — keeps this
inside agentix's static-action connector model with no per-tenant dynamic
tool wiring). Fine for small/medium APIs; a spec with hundreds of
operations is capped and its op list truncated in the description.
"""
from __future__ import annotations

import json

import httpx
import yaml

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.builtin._http_auth import apply_auth
from agentix.connectors.registry import register_connector

_MAX_SPEC_BYTES = 512 * 1024
_MAX_OPS = 150
_DESC_OP_LIMIT = 80  # ops listed in the tool description before truncating

_ACTIONS = [
    ConnectorAction(
        "call_operation",
        "Call one operation from the configured OpenAPI spec. See this tool's description for the "
        "list of operation_id values and their parameters.",
        {
            "type": "object",
            "properties": {
                "operation_id": {"type": "string", "description": "One of the operationId values listed above"},
                "path_params": {"type": "object", "description": "Values for {name} placeholders in the operation's path"},
                "query": {"type": "object", "description": "Query-string parameters"},
                "headers": {"type": "object", "description": "Extra request headers"},
                "json_body": {"type": "object", "description": "JSON request body, when the operation takes one"},
            },
            "required": ["operation_id"],
        },
    ),
]


@register_connector("openapi")
class OpenApiConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="openapi",
        display_name="OpenAPI / Swagger",
        description="Call an API described by an OpenAPI/Swagger spec, with proper auth.",
        category="custom",
        icon="📑",
        auth_type="api_key",
        required_config=["spec"],
        optional_config=[
            "base_url_override", "auth_type", "auth_token", "api_key",
            "api_key_header_name", "api_key_query_name", "username", "password",
        ],
        actions=_ACTIONS,
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._transport: httpx.BaseTransport | None = None  # test-injection hook only
        self._base_url: str = ""
        self._title: str = "the API"
        self._ops: dict[str, dict] = {}

    # -- load -----------------------------------------------------------

    async def _load_spec_text(self) -> str:
        raw = self._require("spec").strip()
        if raw.lower().startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
                r = await c.get(raw)
                r.raise_for_status()
                text = r.text
        else:
            text = raw
        if len(text.encode("utf-8", "ignore")) > _MAX_SPEC_BYTES:
            raise ValueError(
                f"OpenAPI spec is larger than {_MAX_SPEC_BYTES // 1024} KB — "
                "trim it to the operations this agent actually needs."
            )
        return text

    @staticmethod
    def _parse(text: str) -> dict:
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            data = yaml.safe_load(text)
        except Exception as exc:
            raise ValueError(f"could not parse the spec as JSON or YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("the spec did not parse into an object")
        return data

    def _resolve_ref(self, node, doc: dict, _depth: int = 0):
        """Shallow same-document $ref resolution — enough for parameter
        names/locations; deep schema composition isn't needed for a
        description-driven tool."""
        if _depth > 5 or not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target = doc
            for part in ref[2:].split("/"):
                if not isinstance(target, dict):
                    return {}
                target = target.get(part, {})
            return self._resolve_ref(target, doc, _depth + 1)
        return node

    async def connect(self) -> None:
        doc = self._parse(await self._load_spec_text())

        self._title = (doc.get("info") or {}).get("title") or "the API"
        override = self._cfg.get("base_url_override")
        if override:
            self._base_url = override.rstrip("/")
        else:
            servers = doc.get("servers") or []
            self._base_url = (servers[0].get("url", "") if servers else "").rstrip("/")
        # Swagger 2.0 fallback
        if not self._base_url and doc.get("host"):
            scheme = (doc.get("schemes") or ["https"])[0]
            self._base_url = f"{scheme}://{doc['host']}{doc.get('basePath', '')}".rstrip("/")

        methods = ("get", "post", "put", "patch", "delete", "options", "head")
        for path, item in (doc.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            shared_params = item.get("parameters", [])
            for method in methods:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                op_id = op.get("operationId")
                if not op_id:
                    continue
                params = [self._resolve_ref(p, doc) for p in (shared_params + op.get("parameters", []))]
                self._ops[op_id] = {
                    "method": method.upper(),
                    "path": path,
                    "summary": op.get("summary") or op.get("description") or "",
                    "params": [
                        {"name": p.get("name"), "in": p.get("in"), "required": bool(p.get("required"))}
                        for p in params
                        if isinstance(p, dict) and p.get("name")
                    ],
                    "has_body": bool(op.get("requestBody")),
                }
                if len(self._ops) > _MAX_OPS:
                    raise ValueError(
                        f"the spec has more than {_MAX_OPS} operations — this connector "
                        "is for small/medium APIs; point it at a trimmed spec."
                    )

        if not self._ops:
            raise ValueError("no operations with an operationId were found in the spec")

    # -- tool surface -------------------------------------------------

    def tool_schemas(self) -> list[dict]:
        lines = [f"Call operations on {self._title}. `operation_id` must be one of:"]
        for i, (op_id, meta) in enumerate(self._ops.items()):
            if i >= _DESC_OP_LIMIT:
                lines.append(f"…and {len(self._ops) - _DESC_OP_LIMIT} more (ask for the full list if needed).")
                break
            pnames = ", ".join(p["name"] for p in meta["params"]) or "none"
            body = ", body" if meta["has_body"] else ""
            summary = f" — {meta['summary']}" if meta["summary"] else ""
            lines.append(f"- {op_id} ({meta['method']} {meta['path']}){summary} [params: {pnames}{body}]")
        desc = "\n".join(lines)

        schema = dict(_ACTIONS[0].input_schema)
        return [{"name": "openapi__call_operation", "description": desc, "input_schema": schema}]

    # -- call -------------------------------------------------------------

    async def call_operation(
        self,
        operation_id: str,
        path_params: dict | None = None,
        query: dict | None = None,
        headers: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        op = self._ops.get(operation_id)
        if op is None:
            raise ValueError(
                f"unknown operation_id {operation_id!r}. Valid: {', '.join(sorted(self._ops))}"
            )

        path = op["path"]
        for name, value in (path_params or {}).items():
            path = path.replace("{" + str(name) + "}", str(value))
        if "{" in path:
            missing = path[path.index("{") + 1 : path.index("}")] if "}" in path else path
            raise ValueError(f"missing path_params value for {{{missing}}} in {op['path']}")

        url = f"{self._base_url}/{path.lstrip('/')}" if self._base_url else path
        req_headers = {"Accept": "application/json"}
        req_headers.update(headers or {})
        params = dict(query or {})
        basic = apply_auth(self._cfg, req_headers, params)

        async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
            r = await c.request(
                op["method"], url,
                params=params or None,
                headers=req_headers,
                json=json_body if json_body is not None else None,
                auth=basic,
            )
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {"status_code": r.status_code, "ok": r.is_success, "body": body}
