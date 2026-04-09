"""Google Sheets connector — read, write, and manage spreadsheets."""
from __future__ import annotations

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector
from agentix.connectors.builtin._google_auth import _build_client

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

_ACTIONS = [
    ConnectorAction("get_values", "Read a range of cells from a spreadsheet",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Google Sheets spreadsheet ID"},
             "range":          {"type": "string", "description": "A1 notation range (e.g. 'Sheet1!A1:D10')"},
         }, "required": ["spreadsheet_id", "range"]}),

    ConnectorAction("set_values", "Write values to a range of cells",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
             "range":          {"type": "string", "description": "A1 notation range (e.g. 'Sheet1!A1')"},
             "values":         {"type": "array", "items": {"type": "array"}, "description": "2D array of values to write"},
         }, "required": ["spreadsheet_id", "range", "values"]}),

    ConnectorAction("append_rows", "Append rows to the end of a sheet",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
             "range":          {"type": "string", "description": "Sheet name or range (e.g. 'Sheet1')"},
             "values":         {"type": "array", "items": {"type": "array"}, "description": "Rows to append"},
         }, "required": ["spreadsheet_id", "range", "values"]}),

    ConnectorAction("create_spreadsheet", "Create a new Google Sheets spreadsheet",
        {"type": "object",
         "properties": {
             "title":       {"type": "string", "description": "Spreadsheet title"},
             "sheet_names": {"type": "array", "items": {"type": "string"},
                             "description": "Initial sheet tab names (default: ['Sheet1'])"},
         }, "required": ["title"]}),

    ConnectorAction("list_sheets", "List all sheets/tabs in a spreadsheet",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
         }, "required": ["spreadsheet_id"]}),

    ConnectorAction("add_sheet", "Add a new sheet tab to an existing spreadsheet",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
             "title":          {"type": "string", "description": "New sheet tab name"},
         }, "required": ["spreadsheet_id", "title"]}),

    ConnectorAction("clear_range", "Clear all values in a cell range",
        {"type": "object",
         "properties": {
             "spreadsheet_id": {"type": "string", "description": "Spreadsheet ID"},
             "range":          {"type": "string", "description": "A1 notation range to clear"},
         }, "required": ["spreadsheet_id", "range"]}),
]


@register_connector("google_sheets")
class GoogleSheetsConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="google_sheets", display_name="Google Sheets",
        description="Read, write, and manage Google Sheets spreadsheets.",
        category="google", icon="📊", auth_type="oauth2",
        required_config=["credentials_json"],
        optional_config=["access_token", "default_spreadsheet_id"],
        actions=_ACTIONS,
    )

    def _client(self):
        return _build_client(self._cfg, _SCOPES)

    def _spreadsheet_id(self, spreadsheet_id: str = "") -> str:
        sid = spreadsheet_id or self._cfg.get("default_spreadsheet_id", "")
        if not sid:
            raise ValueError("spreadsheet_id is required (or set default_spreadsheet_id in config)")
        return sid

    async def connect(self) -> None:
        sid = self._cfg.get("default_spreadsheet_id", "")
        if sid:
            async with self._client() as c:
                r = await c.get(f"{_BASE}/{sid}", params={"fields": "spreadsheetId"})
                r.raise_for_status()

    async def get_values(self, spreadsheet_id: str, range: str) -> dict:
        async with self._client() as c:
            r = await c.get(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}/values/{range}",
                params={"valueRenderOption": "FORMATTED_VALUE"},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "range": data.get("range", range),
                "values": data.get("values", []),
                "rows": len(data.get("values", [])),
            }

    async def set_values(self, spreadsheet_id: str, range: str, values: list[list]) -> dict:
        body = {"values": values, "majorDimension": "ROWS"}
        async with self._client() as c:
            r = await c.put(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}/values/{range}",
                json=body,
                params={"valueInputOption": "USER_ENTERED"},
            )
            r.raise_for_status()
            d = r.json()
            return {
                "ok": True,
                "updated_cells": d.get("updatedCells", 0),
                "updated_range": d.get("updatedRange", range),
            }

    async def append_rows(self, spreadsheet_id: str, range: str, values: list[list]) -> dict:
        body = {"values": values, "majorDimension": "ROWS"}
        async with self._client() as c:
            r = await c.post(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}/values/{range}:append",
                json=body,
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            )
            r.raise_for_status()
            updates = r.json().get("updates", {})
            return {
                "ok": True,
                "updated_cells": updates.get("updatedCells", 0),
                "updated_range": updates.get("updatedRange", ""),
            }

    async def create_spreadsheet(self, title: str, sheet_names: list[str] | None = None) -> dict:
        sheets = [{"properties": {"title": n}} for n in (sheet_names or ["Sheet1"])]
        body = {"properties": {"title": title}, "sheets": sheets}
        async with self._client() as c:
            r = await c.post(_BASE, json=body)
            r.raise_for_status()
            d = r.json()
            return {
                "ok": True,
                "spreadsheet_id": d.get("spreadsheetId"),
                "url": d.get("spreadsheetUrl"),
                "title": title,
            }

    async def list_sheets(self, spreadsheet_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}",
                params={"fields": "sheets.properties"},
            )
            r.raise_for_status()
            sheets = r.json().get("sheets", [])
            return {
                "sheets": [
                    {
                        "id": s["properties"]["sheetId"],
                        "title": s["properties"]["title"],
                        "index": s["properties"]["index"],
                        "row_count": s["properties"].get("gridProperties", {}).get("rowCount", 0),
                        "column_count": s["properties"].get("gridProperties", {}).get("columnCount", 0),
                    }
                    for s in sheets
                ]
            }

    async def add_sheet(self, spreadsheet_id: str, title: str) -> dict:
        body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        async with self._client() as c:
            r = await c.post(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}:batchUpdate",
                json=body,
            )
            r.raise_for_status()
            replies = r.json().get("replies", [{}])
            props = replies[0].get("addSheet", {}).get("properties", {})
            return {"ok": True, "sheet_id": props.get("sheetId"), "title": title}

    async def clear_range(self, spreadsheet_id: str, range: str) -> dict:
        async with self._client() as c:
            r = await c.post(
                f"{_BASE}/{self._spreadsheet_id(spreadsheet_id)}/values/{range}:clear",
                json={},
            )
            r.raise_for_status()
            return {"ok": True, "cleared_range": r.json().get("clearedRange", range)}
