"""Stripe connector — customers, invoices, payment intents."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("create_customer", "Create a Stripe customer",
        {"type": "object",
         "properties": {
             "email": {"type": "string"}, "name": {"type": "string"},
             "phone": {"type": "string"}, "description": {"type": "string"},
         }, "required": ["email"]}),
    ConnectorAction("get_customer", "Retrieve a Stripe customer by ID",
        {"type": "object",
         "properties": {"customer_id": {"type": "string"}},
         "required": ["customer_id"]}),
    ConnectorAction("create_invoice", "Create and finalize a Stripe invoice",
        {"type": "object",
         "properties": {
             "customer_id": {"type": "string"},
             "amount": {"type": "integer", "description": "Amount in cents"},
             "currency": {"type": "string", "default": "usd"},
             "description": {"type": "string"},
         }, "required": ["customer_id", "amount"]}),
    ConnectorAction("list_payments", "List recent Stripe payment intents",
        {"type": "object",
         "properties": {
             "limit": {"type": "integer", "default": 10},
             "customer_id": {"type": "string"},
         }, "required": []}),
    ConnectorAction("create_payment_intent", "Create a Stripe payment intent",
        {"type": "object",
         "properties": {
             "amount": {"type": "integer"}, "currency": {"type": "string", "default": "usd"},
             "customer_id": {"type": "string"}, "description": {"type": "string"},
         }, "required": ["amount"]}),
]


@register_connector("stripe")
class StripeConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="stripe", display_name="Stripe",
        description="Manage customers, invoices, subscriptions, and payment intents.",
        category="finance", icon="💳", auth_type="api_key",
        required_config=["api_key"], optional_config=[],
        actions=_ACTIONS,
    )

    _BASE = "https://api.stripe.com/v1"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._BASE,
            auth=(self._require("api_key"), ""),
            timeout=30,
        )

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get("/customers", params={"limit": 1})
            r.raise_for_status()

    async def create_customer(self, email: str, name: str = "",
                              phone: str = "", description: str = "") -> dict:
        data: dict = {"email": email}
        if name:
            data["name"] = name
        if phone:
            data["phone"] = phone
        if description:
            data["description"] = description
        async with self._client() as c:
            r = await c.post("/customers", data=data)
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "email": d.get("email"), "name": d.get("name")}

    async def get_customer(self, customer_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"/customers/{customer_id}")
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "email": d.get("email"), "name": d.get("name"),
                    "balance": d.get("balance"), "created": d.get("created")}

    async def create_invoice(self, customer_id: str, amount: int,
                             currency: str = "usd", description: str = "") -> dict:
        async with self._client() as c:
            # Create invoice item
            await c.post("/invoiceitems", data={
                "customer": customer_id, "amount": str(amount),
                "currency": currency, "description": description or "Invoice item",
            })
            # Create and finalize invoice
            r = await c.post("/invoices", data={"customer": customer_id})
            r.raise_for_status()
            inv = r.json()
            fin = await c.post(f"/invoices/{inv['id']}/finalize")
            fin.raise_for_status()
            d = fin.json()
            return {"id": d["id"], "status": d["status"],
                    "amount_due": d["amount_due"], "url": d.get("hosted_invoice_url")}

    async def list_payments(self, limit: int = 10, customer_id: str = "") -> dict:
        params: dict = {"limit": limit}
        if customer_id:
            params["customer"] = customer_id
        async with self._client() as c:
            r = await c.get("/payment_intents", params=params)
            r.raise_for_status()
            return {"payments": [{"id": p["id"], "amount": p["amount"],
                                   "currency": p["currency"], "status": p["status"]}
                                  for p in r.json().get("data", [])]}

    async def create_payment_intent(self, amount: int, currency: str = "usd",
                                    customer_id: str = "", description: str = "") -> dict:
        data: dict = {"amount": str(amount), "currency": currency}
        if customer_id:
            data["customer"] = customer_id
        if description:
            data["description"] = description
        async with self._client() as c:
            r = await c.post("/payment_intents", data=data)
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "client_secret": d["client_secret"], "status": d["status"]}
