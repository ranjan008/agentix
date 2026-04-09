"""Notion connector — create and update pages, query databases."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_page", "Create a new Notion page",
        {"type": "object",
         "properties": {
             "parent_id": {"type": "string", "description": "Parent page or database ID"},
             "parent_type": {"type": "string", "enum": ["page_id", "database_id"], "default": "database_id"},
             "title": {"type": "string"},
             "properties": {"type": "object", "description": "Page properties (for database pages)"},
         }, "required": ["parent_id", "title"]}),
    ConnectorAction("update_page", "Update properties of a Notion page",
        {"type": "object",
         "properties": {
             "page_id": {"type": "string"}, "properties": {"type": "object"},
         }, "required": ["page_id", "properties"]}),
    ConnectorAction("get_page", "Retrieve a Notion page by ID",
        {"type": "object",
         "properties": {"page_id": {"type": "string"}},
         "required": ["page_id"]}),
    ConnectorAction("query_database", "Query a Notion database with optional filters",
        {"type": "object",
         "properties": {
             "database_id": {"type": "string"},
             "filter": {"type": "object"}, "sorts": {"type": "array"},
             "page_size": {"type": "integer", "default": 10},
         }, "required": []}),
    ConnectorAction("append_block", "Append content blocks to a Notion page",
        {"type": "object",
         "properties": {
             "page_id": {"type": "string"}, "text": {"type": "string"},
         }, "required": ["page_id", "text"]}),
]


@register_connector("notion")
class NotionConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="notion", display_name="Notion",
        description="Create and update pages and query databases in Notion.",
        category="productivity", icon="📝", auth_type="api_key",
        required_config=["token"], optional_config=["default_database_id"],
        actions=_ACTIONS,
    )

    _BASE = "https://api.notion.com/v1"
    _VERSION = "2022-06-28"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={
                "Authorization": f"Bearer {self._require('token')}",
                "Notion-Version": self._VERSION,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/users/me")
            r.raise_for_status()

    async def create_page(self, parent_id: str, title: str,
                          parent_type: str = "database_id",
                          properties: dict | None = None) -> dict:
        body: dict = {
            "parent": {parent_type: parent_id},
            "properties": properties or {
                "title": {"title": [{"text": {"content": title}}]}
            },
        }
        async with self._client() as c:
            r = await c.post("/pages", json=body)
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "url": d.get("url")}

    async def update_page(self, page_id: str, properties: dict) -> dict:
        async with self._client() as c:
            r = await c.patch(f"/pages/{page_id}", json={"properties": properties})
            r.raise_for_status()
            return {"id": r.json()["id"], "url": r.json().get("url")}

    async def get_page(self, page_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/pages/{page_id}")
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "url": d.get("url"), "properties": d.get("properties", {})}

    async def query_database(self, database_id: str = "", filter: dict | None = None,
                              sorts: list | None = None, page_size: int = 10) -> dict:
        db_id = database_id or self._cfg.get("default_database_id", "")
        body: dict = {"page_size": page_size}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        async with self._client() as c:
            r = await c.post(f"/databases/{db_id}/query", json=body)
            r.raise_for_status()
            d = r.json()
            return {"results": [{"id": p["id"], "url": p.get("url"), "properties": p.get("properties")}
                                  for p in d.get("results", [])],
                    "has_more": d.get("has_more", False)}

    async def append_block(self, page_id: str, text: str) -> dict:
        children = [{"object": "block", "type": "paragraph",
                     "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}]
        async with self._client() as c:
            r = await c.patch(f"/blocks/{page_id}/children", json={"children": children})
            r.raise_for_status()
            return {"appended": True, "page_id": page_id}
