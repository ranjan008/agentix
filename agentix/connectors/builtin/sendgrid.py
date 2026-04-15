"""SendGrid connector — send email and manage contacts."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("send_email", "Send an email via SendGrid",
        {"type": "object",
         "properties": {
             "to": {"type": "string"}, "subject": {"type": "string"},
             "body": {"type": "string"}, "html": {"type": "string"},
             "from_email": {"type": "string"}, "from_name": {"type": "string"},
         }, "required": ["to", "subject", "body"]}),
    ConnectorAction("send_template", "Send a SendGrid dynamic template email",
        {"type": "object",
         "properties": {
             "to": {"type": "string"}, "template_id": {"type": "string"},
             "dynamic_data": {"type": "object"},
             "from_email": {"type": "string"},
         }, "required": ["to", "template_id"]}),
    ConnectorAction("add_contact", "Add or update a contact in SendGrid",
        {"type": "object",
         "properties": {
             "email": {"type": "string"}, "first_name": {"type": "string"},
             "last_name": {"type": "string"}, "list_ids": {"type": "array",
                 "items": {"type": "string"}},
         }, "required": ["email"]}),
    ConnectorAction("get_stats", "Get SendGrid send statistics",
        {"type": "object",
         "properties": {
             "start_date": {"type": "string", "description": "YYYY-MM-DD"},
             "end_date": {"type": "string"},
         }, "required": ["start_date"]}),
]


@register_connector("sendgrid")
class SendGridConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="sendgrid", display_name="SendGrid",
        description="Send transactional and template-based emails via SendGrid.",
        category="messaging", icon="📧", auth_type="api_key",
        required_config=["api_key"], optional_config=["from_email", "from_name"],
        actions=_ACTIONS,
    )

    _BASE = "https://api.sendgrid.com/v3"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={"Authorization": f"Bearer {self._require('api_key')}",
                     "Content-Type": "application/json"},
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/user/profile")
            r.raise_for_status()

    async def send_email(self, to: str, subject: str, body: str,
                         html: str = "", from_email: str = "", from_name: str = "") -> dict:
        sender_email = from_email or self._cfg.get("from_email", "noreply@agentix.dev")
        sender_name = from_name or self._cfg.get("from_name", "Agentix")
        content = [{"type": "text/plain", "value": body}]
        if html:
            content.append({"type": "text/html", "value": html})
        payload = {
            "personalizations": [{"to": [{"email": to}]}],
            "from": {"email": sender_email, "name": sender_name},
            "subject": subject, "content": content,
        }
        async with self._client() as c:
            r = await c.post("/mail/send", json=payload)
            r.raise_for_status()
            return {"sent": True, "to": to, "subject": subject}

    async def send_template(self, to: str, template_id: str,
                            dynamic_data: dict | None = None, from_email: str = "") -> dict:
        sender = from_email or self._cfg.get("from_email", "noreply@agentix.dev")
        payload = {
            "personalizations": [{"to": [{"email": to}],
                                   "dynamic_template_data": dynamic_data or {}}],
            "from": {"email": sender},
            "template_id": template_id,
        }
        async with self._client() as c:
            r = await c.post("/mail/send", json=payload)
            r.raise_for_status()
            return {"sent": True, "template_id": template_id, "to": to}

    async def add_contact(self, email: str, first_name: str = "",
                          last_name: str = "", list_ids: list | None = None) -> dict:
        contact: dict = {"email": email}
        if first_name:
            contact["first_name"] = first_name
        if last_name:
            contact["last_name"] = last_name
        body: dict = {"contacts": [contact]}
        if list_ids:
            body["list_ids"] = list_ids
        async with self._client() as c:
            r = await c.put("/marketing/contacts", json=body)
            r.raise_for_status()
            return {"job_id": r.json().get("job_id"), "email": email}

    async def get_stats(self, start_date: str, end_date: str = "") -> dict:
        params: dict = {"start_date": start_date}
        if end_date:
            params["end_date"] = end_date
        async with self._client() as c:
            r = await c.get("/stats", params=params)
            r.raise_for_status()
            data = r.json()
            if data:
                stats = data[0].get("stats", [{}])[0].get("metrics", {})
                return {"delivered": stats.get("delivered", 0), "opens": stats.get("opens", 0),
                        "clicks": stats.get("clicks", 0), "bounces": stats.get("bounces", 0)}
            return {}
