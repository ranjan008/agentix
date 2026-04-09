"""GitHub connector — issues, PRs, code search, file retrieval."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_issue", "Create a new GitHub issue",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "repo": {"type": "string"},
             "title": {"type": "string"}, "body": {"type": "string"},
             "labels": {"type": "array", "items": {"type": "string"}},
         }, "required": ["title"]}),
    ConnectorAction("get_issue", "Get a GitHub issue by number",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "repo": {"type": "string"},
             "issue_number": {"type": "integer"},
         }, "required": ["issue_number"]}),
    ConnectorAction("add_comment", "Add a comment to a GitHub issue or PR",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "repo": {"type": "string"},
             "issue_number": {"type": "integer"}, "body": {"type": "string"},
         }, "required": ["issue_number", "body"]}),
    ConnectorAction("create_pull_request", "Create a GitHub pull request",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "repo": {"type": "string"},
             "title": {"type": "string"}, "body": {"type": "string"},
             "head": {"type": "string", "description": "Source branch"},
             "base": {"type": "string", "description": "Target branch"},
         }, "required": ["title", "head", "base"]}),
    ConnectorAction("list_repos", "List repositories for an owner",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "type": {"type": "string", "enum": ["all", "owner", "public"]},
         }, "required": []}),
    ConnectorAction("search_code", "Search code across GitHub",
        {"type": "object",
         "properties": {
             "query": {"type": "string", "description": "GitHub code search query"},
             "limit": {"type": "integer", "default": 10},
         }, "required": ["query"]}),
    ConnectorAction("get_file", "Get file contents from a repository",
        {"type": "object",
         "properties": {
             "owner": {"type": "string"}, "repo": {"type": "string"},
             "path": {"type": "string"}, "ref": {"type": "string", "description": "Branch or commit SHA"},
         }, "required": ["path"]}),
]


@register_connector("github")
class GitHubConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="github", display_name="GitHub",
        description="Create issues, pull requests, and manage repositories.",
        category="developer", icon="🐙", auth_type="api_key",
        required_config=["token"],
        optional_config=["default_owner", "default_repo", "base_url"],
        actions=_ACTIONS,
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._base = self._cfg.get("base_url", "https://api.github.com").rstrip("/")
        self._default_owner = self._cfg.get("default_owner", "")
        self._default_repo = self._cfg.get("default_repo", "")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {self._require('token')}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )

    def _owner(self, owner: str) -> str:
        return owner or self._default_owner

    def _repo(self, repo: str) -> str:
        return repo or self._default_repo

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/user")
            r.raise_for_status()

    async def create_issue(self, title: str, body: str = "", owner: str = "",
                           repo: str = "", labels: list | None = None) -> dict:
        async with self._client() as c:
            r = await c.post(
                f"/repos/{self._owner(owner)}/{self._repo(repo)}/issues",
                json={"title": title, "body": body, "labels": labels or []},
            )
            r.raise_for_status()
            d = r.json()
            return {"number": d["number"], "url": d["html_url"], "title": d["title"]}

    async def get_issue(self, issue_number: int, owner: str = "", repo: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"/repos/{self._owner(owner)}/{self._repo(repo)}/issues/{issue_number}")
            r.raise_for_status()
            d = r.json()
            return {"number": d["number"], "title": d["title"], "state": d["state"],
                    "body": d.get("body", ""), "url": d["html_url"]}

    async def add_comment(self, issue_number: int, body: str, owner: str = "", repo: str = "") -> dict:
        async with self._client() as c:
            r = await c.post(
                f"/repos/{self._owner(owner)}/{self._repo(repo)}/issues/{issue_number}/comments",
                json={"body": body},
            )
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "url": d["html_url"]}

    async def create_pull_request(self, title: str, head: str, base: str,
                                  body: str = "", owner: str = "", repo: str = "") -> dict:
        async with self._client() as c:
            r = await c.post(
                f"/repos/{self._owner(owner)}/{self._repo(repo)}/pulls",
                json={"title": title, "head": head, "base": base, "body": body},
            )
            r.raise_for_status()
            d = r.json()
            return {"number": d["number"], "url": d["html_url"], "state": d["state"]}

    async def list_repos(self, owner: str = "", type: str = "all") -> dict:
        async with self._client() as c:
            path = f"/users/{self._owner(owner)}/repos" if self._owner(owner) else "/user/repos"
            r = await c.get(path, params={"type": type, "per_page": 30})
            r.raise_for_status()
            return {"repos": [{"name": x["name"], "url": x["html_url"], "private": x["private"]}
                               for x in r.json()]}

    async def search_code(self, query: str, limit: int = 10) -> dict:
        async with self._client() as c:
            r = await c.get("/search/code", params={"q": query, "per_page": limit})
            r.raise_for_status()
            items = r.json().get("items", [])
            return {"total": r.json().get("total_count", 0),
                    "results": [{"path": x["path"], "repo": x["repository"]["full_name"],
                                 "url": x["html_url"]} for x in items]}

    async def get_file(self, path: str, owner: str = "", repo: str = "", ref: str = "HEAD") -> dict:
        import base64
        async with self._client() as c:
            r = await c.get(f"/repos/{self._owner(owner)}/{self._repo(repo)}/contents/{path}",
                            params={"ref": ref})
            r.raise_for_status()
            d = r.json()
            content = base64.b64decode(d["content"]).decode() if d.get("encoding") == "base64" else d.get("content", "")
            return {"path": d["path"], "sha": d["sha"], "content": content, "url": d["html_url"]}
