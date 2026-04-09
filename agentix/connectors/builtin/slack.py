"""Slack connector — post messages, manage channels and users."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("post_message", "Post a message to a Slack channel",
        {"type": "object",
         "properties": {
             "channel": {"type": "string", "description": "Channel ID or #name"},
             "text": {"type": "string"}, "blocks": {"type": "array"},
         }, "required": ["text"]}),
    ConnectorAction("create_channel", "Create a new Slack channel",
        {"type": "object",
         "properties": {
             "name": {"type": "string"}, "is_private": {"type": "boolean", "default": False},
         }, "required": ["name"]}),
    ConnectorAction("get_user_info", "Get information about a Slack user",
        {"type": "object",
         "properties": {"user_id": {"type": "string"}},
         "required": ["user_id"]}),
    ConnectorAction("list_channels", "List Slack channels",
        {"type": "object",
         "properties": {"types": {"type": "string", "default": "public_channel"},
                        "limit": {"type": "integer", "default": 20}},
         "required": []}),
    ConnectorAction("upload_file", "Upload a text file/snippet to Slack",
        {"type": "object",
         "properties": {
             "channel": {"type": "string"}, "content": {"type": "string"},
             "filename": {"type": "string"}, "title": {"type": "string"},
         }, "required": ["channel", "content"]}),
]


@register_connector("slack")
class SlackConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="slack", display_name="Slack",
        description="Post messages, create channels, and interact with your Slack workspace.",
        category="messaging", icon="💬", auth_type="api_key",
        required_config=["bot_token"], optional_config=["default_channel"],
        actions=_ACTIONS,
    )

    _BASE = "https://slack.com/api"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={"Authorization": f"Bearer {self._require('bot_token')}"},
            timeout=30,
        )

    def _channel(self, ch: str) -> str:
        return ch or self._cfg.get("default_channel", "")

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.post("/auth.test")
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error", "Slack auth failed"))

    async def post_message(self, text: str, channel: str = "", blocks: list | None = None) -> dict:
        payload: dict = {"channel": self._channel(channel), "text": text}
        if blocks:
            payload["blocks"] = blocks
        async with self._client() as c:
            r = await c.post("/chat.postMessage", json=payload)
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error"))
            return {"ts": d["ts"], "channel": d["channel"]}

    async def create_channel(self, name: str, is_private: bool = False) -> dict:
        async with self._client() as c:
            r = await c.post("/conversations.create",
                             json={"name": name, "is_private": is_private})
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error"))
            ch = d["channel"]
            return {"id": ch["id"], "name": ch["name"]}

    async def get_user_info(self, user_id: str) -> dict:
        async with self._client() as c:
            r = await c.get("/users.info", params={"user": user_id})
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error"))
            u = d["user"]
            p = u.get("profile", {})
            return {"id": u["id"], "name": u["name"], "real_name": p.get("real_name"),
                    "email": p.get("email"), "title": p.get("title")}

    async def list_channels(self, types: str = "public_channel", limit: int = 20) -> dict:
        async with self._client() as c:
            r = await c.get("/conversations.list", params={"types": types, "limit": limit})
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error"))
            return {"channels": [{"id": ch["id"], "name": ch["name"],
                                   "is_private": ch.get("is_private")}
                                  for ch in d.get("channels", [])]}

    async def upload_file(self, channel: str, content: str,
                          filename: str = "output.txt", title: str = "") -> dict:
        async with self._client() as c:
            r = await c.post("/files.upload",
                             data={"channels": channel, "content": content,
                                   "filename": filename, "title": title})
            d = r.json()
            if not d.get("ok"):
                raise ValueError(d.get("error"))
            return {"file_id": d["file"]["id"], "permalink": d["file"].get("permalink")}
