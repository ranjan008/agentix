"""Twilio connector — SMS, voice calls, message history."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("send_sms", "Send an SMS message via Twilio",
        {"type": "object",
         "properties": {
             "to": {"type": "string"}, "body": {"type": "string"},
             "from_number": {"type": "string"},
         }, "required": ["to", "body"]}),
    ConnectorAction("make_call", "Initiate a voice call via Twilio",
        {"type": "object",
         "properties": {
             "to": {"type": "string"}, "twiml_url": {"type": "string"},
             "from_number": {"type": "string"},
         }, "required": ["to", "twiml_url"]}),
    ConnectorAction("list_messages", "List recent Twilio messages",
        {"type": "object",
         "properties": {
             "limit": {"type": "integer", "default": 10},
             "to": {"type": "string"}, "from_": {"type": "string"},
         }, "required": []}),
    ConnectorAction("get_message", "Get a Twilio message by SID",
        {"type": "object",
         "properties": {"message_sid": {"type": "string"}},
         "required": ["message_sid"]}),
]


@register_connector("twilio")
class TwilioConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="twilio", display_name="Twilio",
        description="Send SMS messages, make calls, and manage phone numbers.",
        category="messaging", icon="📱", auth_type="api_key",
        required_config=["account_sid", "auth_token"], optional_config=["from_number"],
        actions=_ACTIONS,
    )

    def _base_url(self) -> str:
        sid = self._require("account_sid")
        return f"https://api.twilio.com/2010-04-01/Accounts/{sid}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url(),
            auth=(self._require("account_sid"), self._require("auth_token")),
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get(".json")
            r.raise_for_status()

    async def send_sms(self, to: str, body: str, from_number: str = "") -> dict:
        fr = from_number or self._cfg.get("from_number", "")
        async with self._client() as c:
            r = await c.post("/Messages.json",
                             data={"To": to, "From": fr, "Body": body})
            r.raise_for_status()
            d = r.json()
            return {"sid": d["sid"], "status": d["status"], "to": d["to"]}

    async def make_call(self, to: str, twiml_url: str, from_number: str = "") -> dict:
        fr = from_number or self._cfg.get("from_number", "")
        async with self._client() as c:
            r = await c.post("/Calls.json",
                             data={"To": to, "From": fr, "Url": twiml_url})
            r.raise_for_status()
            d = r.json()
            return {"sid": d["sid"], "status": d["status"]}

    async def list_messages(self, limit: int = 10, to: str = "", from_: str = "") -> dict:
        params: dict = {"PageSize": limit}
        if to: params["To"] = to
        if from_: params["From"] = from_
        async with self._client() as c:
            r = await c.get("/Messages.json", params=params)
            r.raise_for_status()
            return {"messages": [{"sid": m["sid"], "to": m["to"], "from": m["from"],
                                   "body": m["body"], "status": m["status"]}
                                  for m in r.json().get("messages", [])]}

    async def get_message(self, message_sid: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/Messages/{message_sid}.json")
            r.raise_for_status()
            d = r.json()
            return {"sid": d["sid"], "to": d["to"], "from": d["from"],
                    "body": d["body"], "status": d["status"], "date_sent": d.get("date_sent")}
