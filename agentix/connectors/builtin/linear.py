"""Linear connector — issues, teams, projects via GraphQL API."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_issue", "Create a Linear issue",
        {"type": "object",
         "properties": {
             "title": {"type": "string"}, "description": {"type": "string"},
             "team_id": {"type": "string"}, "priority": {"type": "integer", "description": "0=no priority,1=urgent,2=high,3=medium,4=low"},
             "label_ids": {"type": "array", "items": {"type": "string"}},
         }, "required": ["title"]}),
    ConnectorAction("get_issue", "Get a Linear issue by ID",
        {"type": "object",
         "properties": {"issue_id": {"type": "string"}},
         "required": ["issue_id"]}),
    ConnectorAction("update_issue", "Update a Linear issue",
        {"type": "object",
         "properties": {
             "issue_id": {"type": "string"}, "title": {"type": "string"},
             "description": {"type": "string"}, "state_id": {"type": "string"},
             "priority": {"type": "integer"},
         }, "required": ["issue_id"]}),
    ConnectorAction("list_teams", "List Linear teams in the workspace",
        {"type": "object", "properties": {}, "required": []}),
    ConnectorAction("list_issues", "List Linear issues with optional filter",
        {"type": "object",
         "properties": {
             "team_id": {"type": "string"}, "state": {"type": "string"},
             "limit": {"type": "integer", "default": 10},
         }, "required": []}),
]


@register_connector("linear")
class LinearConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="linear", display_name="Linear",
        description="Create and manage issues, projects, and cycles in Linear.",
        category="developer", icon="📐", auth_type="api_key",
        required_config=["api_key"], optional_config=["default_team_id"],
        actions=_ACTIONS,
    )

    _URL = "https://api.linear.app/graphql"

    def _headers(self) -> dict:
        return {"Authorization": self._require("api_key"), "Content-Type": "application/json"}

    async def _gql(self, query: str, variables: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(self._URL, json={"query": query, "variables": variables or {}},
                             headers=self._headers())
            r.raise_for_status()
            body = r.json()
            if "errors" in body:
                raise ValueError(body["errors"][0]["message"])
            return body.get("data", {})

    async def connect(self) -> None:
        await self._gql("{ viewer { id name } }")

    async def create_issue(self, title: str, description: str = "", team_id: str = "",
                           priority: int = 0, label_ids: list | None = None) -> dict:
        tid = team_id or self._cfg.get("default_team_id", "")
        data = await self._gql(
            "mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier title url}}}",
            {"input": {"title": title, "description": description, "teamId": tid,
                       "priority": priority, **({"labelIds": label_ids} if label_ids else {})}}
        )
        issue = data["issueCreate"]["issue"]
        return {"id": issue["id"], "identifier": issue["identifier"],
                "title": issue["title"], "url": issue["url"]}

    async def get_issue(self, issue_id: str) -> dict:
        data = await self._gql(
            "query($id:String!){issue(id:$id){id identifier title description state{name} priority url}}",
            {"id": issue_id}
        )
        i = data["issue"]
        return {"id": i["id"], "identifier": i["identifier"], "title": i["title"],
                "description": i.get("description"), "state": i["state"]["name"],
                "priority": i["priority"], "url": i["url"]}

    async def update_issue(self, issue_id: str, title: str = "", description: str = "",
                           state_id: str = "", priority: int | None = None) -> dict:
        inp: dict = {}
        if title: inp["title"] = title
        if description: inp["description"] = description
        if state_id: inp["stateId"] = state_id
        if priority is not None: inp["priority"] = priority
        data = await self._gql(
            "mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier}}}",
            {"id": issue_id, "input": inp}
        )
        return {"id": issue_id, "updated": data["issueUpdate"]["success"]}

    async def list_teams(self) -> dict:
        data = await self._gql("{teams{nodes{id name key description}}}")
        return {"teams": [{"id": t["id"], "name": t["name"], "key": t["key"]}
                           for t in data["teams"]["nodes"]]}

    async def list_issues(self, team_id: str = "", state: str = "", limit: int = 10) -> dict:
        tid = team_id or self._cfg.get("default_team_id", "")
        filter_arg = f'(filter:{{team:{{id:{{eq:"{tid}"}}}}}})' if tid else ""
        data = await self._gql(
            f"{{issues{filter_arg}{{nodes{{id identifier title state{{name}} priority url}}}}}}"
        )
        issues = data["issues"]["nodes"][:limit]
        return {"issues": [{"id": i["id"], "identifier": i["identifier"], "title": i["title"],
                             "state": i["state"]["name"], "url": i["url"]} for i in issues]}
