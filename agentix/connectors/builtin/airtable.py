"""Airtable connector — read and write records."""
from __future__ import annotations
import httpx
from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector

_ACTIONS = [
    ConnectorAction("list_records", "List records from an Airtable table",
        {"type": "object",
         "properties": {
             "table": {"type": "string"}, "filter_formula": {"type": "string"},
             "max_records": {"type": "integer", "default": 20},
             "sort": {"type": "array"},
         }, "required": []}),
    ConnectorAction("get_record", "Get a specific Airtable record by ID",
        {"type": "object",
         "properties": {
             "table": {"type": "string"}, "record_id": {"type": "string"},
         }, "required": ["record_id"]}),
    ConnectorAction("create_record", "Create a new Airtable record",
        {"type": "object",
         "properties": {
             "table": {"type": "string"}, "fields": {"type": "object"},
         }, "required": ["fields"]}),
    ConnectorAction("update_record", "Update fields on an Airtable record",
        {"type": "object",
         "properties": {
             "table": {"type": "string"}, "record_id": {"type": "string"},
             "fields": {"type": "object"},
         }, "required": ["record_id", "fields"]}),
    ConnectorAction("delete_record", "Delete an Airtable record",
        {"type": "object",
         "properties": {
             "table": {"type": "string"}, "record_id": {"type": "string"},
         }, "required": ["record_id"]}),
]


@register_connector("airtable")
class AirtableConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="airtable", display_name="Airtable",
        description="Read and write records in Airtable bases and tables.",
        category="database", icon="🗄️", auth_type="api_key",
        required_config=["api_key", "base_id"], optional_config=["default_table"],
        actions=_ACTIONS,
    )

    _BASE = "https://api.airtable.com/v0"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self._BASE}/{self._require('base_id')}",
            headers={"Authorization": f"Bearer {self._require('api_key')}",
                     "Content-Type": "application/json"},
            timeout=30,
        )

    def _table(self, t: str) -> str:
        return t or self._cfg.get("default_table", "")

    async def connect(self) -> None:
        table = self._cfg.get("default_table", "")
        if table:
            async with self._client() as c:
                r = await c.get(f"/{table}", params={"maxRecords": 1})
                r.raise_for_status()

    async def list_records(self, table: str = "", filter_formula: str = "",
                           max_records: int = 20, sort: list | None = None) -> dict:
        params: dict = {"maxRecords": max_records}
        if filter_formula:
            params["filterByFormula"] = filter_formula
        if sort:
            params["sort"] = sort
        async with self._client() as c:
            r = await c.get(f"/{self._table(table)}", params=params)
            r.raise_for_status()
            return {"records": [{"id": rec["id"], "fields": rec["fields"]}
                                  for rec in r.json().get("records", [])]}

    async def get_record(self, record_id: str, table: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"/{self._table(table)}/{record_id}")
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "fields": d["fields"]}

    async def create_record(self, fields: dict, table: str = "") -> dict:
        async with self._client() as c:
            r = await c.post(f"/{self._table(table)}", json={"fields": fields})
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "fields": d["fields"]}

    async def update_record(self, record_id: str, fields: dict, table: str = "") -> dict:
        async with self._client() as c:
            r = await c.patch(f"/{self._table(table)}/{record_id}", json={"fields": fields})
            r.raise_for_status()
            d = r.json()
            return {"id": d["id"], "fields": d["fields"]}

    async def delete_record(self, record_id: str, table: str = "") -> dict:
        async with self._client() as c:
            r = await c.delete(f"/{self._table(table)}/{record_id}")
            r.raise_for_status()
            return {"deleted": r.json().get("deleted", True), "id": record_id}
