"""HubSpot connector — contacts, deals, companies."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_contact", "Create a new HubSpot contact",
        {"type": "object",
         "properties": {
             "email": {"type": "string"}, "firstname": {"type": "string"},
             "lastname": {"type": "string"}, "phone": {"type": "string"},
             "company": {"type": "string"},
         }, "required": ["email"]}),
    ConnectorAction("get_contact", "Get a HubSpot contact by ID or email",
        {"type": "object",
         "properties": {
             "contact_id": {"type": "string"},
             "email": {"type": "string"},
         }, "required": []}),
    ConnectorAction("update_contact", "Update a HubSpot contact",
        {"type": "object",
         "properties": {
             "contact_id": {"type": "string"},
             "properties": {"type": "object"},
         }, "required": ["contact_id", "properties"]}),
    ConnectorAction("create_deal", "Create a new HubSpot deal",
        {"type": "object",
         "properties": {
             "dealname": {"type": "string"},
             "amount": {"type": "number"},
             "dealstage": {"type": "string"},
             "pipeline": {"type": "string"},
             "contact_id": {"type": "string"},
         }, "required": ["dealname"]}),
    ConnectorAction("search_contacts", "Search HubSpot contacts",
        {"type": "object",
         "properties": {
             "query": {"type": "string"}, "limit": {"type": "integer", "default": 10},
         }, "required": ["query"]}),
]


@register_connector("hubspot")
class HubSpotConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="hubspot", display_name="HubSpot",
        description="Manage contacts, deals, and companies in HubSpot CRM.",
        category="crm", icon="🧡", auth_type="api_key",
        required_config=["api_key"], optional_config=[],
        actions=_ACTIONS,
    )

    _BASE = "https://api.hubapi.com"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            headers={"Authorization": f"Bearer {self._require('api_key')}",
                     "Content-Type": "application/json"},
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/crm/v3/objects/contacts", params={"limit": 1})
            r.raise_for_status()

    async def create_contact(self, email: str, firstname: str = "", lastname: str = "",
                             phone: str = "", company: str = "") -> dict:
        props: dict = {"email": email}
        if firstname: props["firstname"] = firstname
        if lastname: props["lastname"] = lastname
        if phone: props["phone"] = phone
        if company: props["company"] = company
        async with self._client() as c:
            r = await c.post("/crm/v3/objects/contacts", json={"properties": props})
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "email": email}

    async def get_contact(self, contact_id: str = "", email: str = "") -> dict:
        async with self._client() as c:
            if contact_id:
                r = await c.get(f"/crm/v3/objects/contacts/{contact_id}",
                                params={"properties": "email,firstname,lastname,phone,company"})
                r.raise_for_status()
                d = r.json()
                return {"id": d["id"], **d.get("properties", {})}
            else:
                r = await c.post("/crm/v3/objects/contacts/search",
                                  json={"filterGroups": [{"filters": [
                                      {"propertyName": "email", "operator": "EQ", "value": email}
                                  ]}]})
                r.raise_for_status()
                results = r.json().get("results", [])
                return results[0] if results else {}

    async def update_contact(self, contact_id: str, properties: dict) -> dict:
        async with self._client() as c:
            r = await c.patch(f"/crm/v3/objects/contacts/{contact_id}",
                              json={"properties": properties})
            r.raise_for_status()
            return {"id": contact_id, "updated": True}

    async def create_deal(self, dealname: str, amount: float = 0,
                          dealstage: str = "appointmentscheduled",
                          pipeline: str = "default", contact_id: str = "") -> dict:
        async with self._client() as c:
            r = await c.post("/crm/v3/objects/deals", json={"properties": {
                "dealname": dealname, "amount": str(amount),
                "dealstage": dealstage, "pipeline": pipeline,
            }})
            r.raise_for_status()
            deal = r.json()
            if contact_id:
                await c.put(f"/crm/v3/objects/deals/{deal['id']}/associations/contacts/{contact_id}/deal_to_contact")
            return {"id": deal["id"], "dealname": dealname}

    async def search_contacts(self, query: str, limit: int = 10) -> dict:
        async with self._client() as c:
            r = await c.post("/crm/v3/objects/contacts/search", json={
                "query": query, "limit": limit,
                "properties": ["email", "firstname", "lastname", "company"],
            })
            r.raise_for_status()
            return {"results": r.json().get("results", []),
                    "total": r.json().get("total", 0)}
