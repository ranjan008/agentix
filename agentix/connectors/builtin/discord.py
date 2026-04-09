"""Discord connector — messages, threads, channels."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("send_message", "Send a message to a Discord channel",
        {"type": "object",
         "properties": {
             "channel_id": {"type": "string"}, "content": {"type": "string"},
             "embeds": {"type": "array"},
         }, "required": ["content"]}),
    ConnectorAction("create_thread", "Create a thread in a Discord channel",
        {"type": "object",
         "properties": {
             "channel_id": {"type": "string"}, "name": {"type": "string"},
             "message": {"type": "string"},
             "auto_archive_duration": {"type": "integer", "default": 1440},
         }, "required": ["name", "message"]}),
    ConnectorAction("list_channels", "List channels in the Discord guild",
        {"type": "object",
         "properties": {"guild_id": {"type": "string"}},
         "required": []}),
    ConnectorAction("get_guild_info", "Get information about the Discord guild",
        {"type": "object",
         "properties": {"guild_id": {"type": "string"}},
         "required": []}),
]


@register_connector("discord")
class DiscordConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="discord", display_name="Discord",
        description="Post messages, manage roles, and interact with Discord guilds.",
        category="messaging", icon="🎮", auth_type="api_key",
        required_config=["bot_token"],
        optional_config=["default_channel_id", "guild_id"],
        actions=_ACTIONS,
    )

    _BASE = "https://discord.com/api/v10"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={"Authorization": f"Bot {self._require('bot_token')}",
                     "Content-Type": "application/json"},
            timeout=30,
        )

    def _channel(self, ch: str) -> str:
        return ch or self._cfg.get("default_channel_id", "")

    def _guild(self, g: str) -> str:
        return g or self._cfg.get("guild_id", "")

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/users/@me")
            r.raise_for_status()

    async def send_message(self, content: str, channel_id: str = "",
                           embeds: list | None = None) -> dict:
        payload: dict = {"content": content}
        if embeds:
            payload["embeds"] = embeds
        async with self._client() as c:
            r = await c.post(f"/channels/{self._channel(channel_id)}/messages", json=payload)
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "channel_id": d["channel_id"]}

    async def create_thread(self, name: str, message: str, channel_id: str = "",
                            auto_archive_duration: int = 1440) -> dict:
        async with self._client() as c:
            # First send starter message
            msg_r = await c.post(f"/channels/{self._channel(channel_id)}/messages",
                                  json={"content": message})
            msg_r.raise_for_status()
            msg_id = msg_r.json()["id"]
            # Create thread from message
            r = await c.post(
                f"/channels/{self._channel(channel_id)}/messages/{msg_id}/threads",
                json={"name": name, "auto_archive_duration": auto_archive_duration},
            )
            r.raise_for_status()
            d = r.json()
            return {"thread_id": d["id"], "name": d["name"]}

    async def list_channels(self, guild_id: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"/guilds/{self._guild(guild_id)}/channels")
            r.raise_for_status()
            return {"channels": [{"id": ch["id"], "name": ch["name"], "type": ch["type"]}
                                  for ch in r.json()]}

    async def get_guild_info(self, guild_id: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"/guilds/{self._guild(guild_id)}")
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "name": d["name"], "member_count": d.get("approximate_member_count"),
                    "description": d.get("description")}
