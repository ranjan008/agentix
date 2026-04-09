"""Jira connector — issues, projects, comments."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_issue", "Create a Jira issue",
        {"type": "object",
         "properties": {
             "project": {"type": "string"}, "summary": {"type": "string"},
             "description": {"type": "string"},
             "issue_type": {"type": "string", "default": "Task"},
             "priority": {"type": "string"},
         }, "required": ["summary"]}),
    ConnectorAction("get_issue", "Get a Jira issue by key",
        {"type": "object",
         "properties": {"issue_key": {"type": "string"}},
         "required": ["issue_key"]}),
    ConnectorAction("update_issue", "Update fields of a Jira issue",
        {"type": "object",
         "properties": {
             "issue_key": {"type": "string"}, "summary": {"type": "string"},
             "description": {"type": "string"}, "status": {"type": "string"},
         }, "required": ["issue_key"]}),
    ConnectorAction("add_comment", "Add a comment to a Jira issue",
        {"type": "object",
         "properties": {
             "issue_key": {"type": "string"}, "body": {"type": "string"},
         }, "required": ["issue_key", "body"]}),
    ConnectorAction("list_projects", "List Jira projects",
        {"type": "object", "properties": {}, "required": []}),
]


@register_connector("jira")
class JiraConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="jira", display_name="Jira",
        description="Create and manage Jira issues, projects, and sprints.",
        category="developer", icon="🎯", auth_type="api_key",
        required_config=["base_url", "email", "api_token"],
        optional_config=["default_project"],
        actions=_ACTIONS,
    )

    def _client(self) -> httpx.AsyncClient:
        base = self._require("base_url").rstrip("/")
        return httpx.AsyncClient(
            base_url=f"{base}/rest/api/3",
            auth=(self._require("email"), self._require("api_token")),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/myself")
            r.raise_for_status()

    async def create_issue(self, summary: str, project: str = "", description: str = "",
                           issue_type: str = "Task", priority: str = "") -> dict:
        proj = project or self._cfg.get("default_project", "")
        body: dict = {
            "fields": {
                "project": {"key": proj},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
        }
        if description:
            body["fields"]["description"] = {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": description}]}]
            }
        if priority:
            body["fields"]["priority"] = {"name": priority}
        async with self._client() as c:
            r = await c.post("/issue", json=body)
            r.raise_for_status()
            d = r.json()
            return {"key": d["key"], "id": d["id"],
                    "url": f"{self._require('base_url').rstrip('/')}/browse/{d['key']}"}

    async def get_issue(self, issue_key: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/issue/{issue_key}")
            r.raise_for_status()
            d = r.json()
            f = d.get("fields", {})
            return {"key": d["key"], "summary": f.get("summary"),
                    "status": f.get("status", {}).get("name"),
                    "assignee": (f.get("assignee") or {}).get("displayName"),
                    "url": f"{self._require('base_url').rstrip('/')}/browse/{d['key']}"}

    async def update_issue(self, issue_key: str, summary: str = "",
                           description: str = "", status: str = "") -> dict:
        fields: dict = {}
        if summary:
            fields["summary"] = summary
        async with self._client() as c:
            if fields:
                r = await c.put(f"/issue/{issue_key}", json={"fields": fields})
                r.raise_for_status()
            return {"key": issue_key, "updated": True}

    async def add_comment(self, issue_key: str, body: str) -> dict:
        payload = {
            "body": {
                "type": "doc", "version": 1,
                "content": [{"type": "paragraph",
                              "content": [{"type": "text", "text": body}]}]
            }
        }
        async with self._client() as c:
            r = await c.post(f"/issue/{issue_key}/comment", json=payload)
            r.raise_for_status()
            return {"id": r.json()["id"]}

    async def list_projects(self) -> dict:
        async with self._client() as c:
            r = await c.get("/project")
            r.raise_for_status()
            return {"projects": [{"key": p["key"], "name": p["name"]}
                                  for p in r.json()]}
