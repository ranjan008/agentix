"""Google Calendar connector — manage events and calendars."""
from __future__ import annotations

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector
from agentix.connectors.builtin._google_auth import _build_client

_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

_BASE = "https://www.googleapis.com/calendar/v3"

_ACTIONS = [
    ConnectorAction("list_events", "List upcoming calendar events",
        {"type": "object",
         "properties": {
             "calendar_id":  {"type": "string", "description": "Calendar ID (default: 'primary')"},
             "time_min":     {"type": "string", "description": "Start time in ISO 8601 (e.g. '2025-01-01T00:00:00Z')"},
             "time_max":     {"type": "string", "description": "End time in ISO 8601"},
             "max_results":  {"type": "integer", "description": "Max events to return (default 10)"},
             "query":        {"type": "string", "description": "Free-text search across event fields"},
         }, "required": []}),

    ConnectorAction("get_event", "Get a specific calendar event by ID",
        {"type": "object",
         "properties": {
             "event_id":    {"type": "string", "description": "Calendar event ID"},
             "calendar_id": {"type": "string", "description": "Calendar ID (default: 'primary')"},
         }, "required": ["event_id"]}),

    ConnectorAction("create_event", "Create a new calendar event",
        {"type": "object",
         "properties": {
             "summary":     {"type": "string", "description": "Event title"},
             "start":       {"type": "string", "description": "Start datetime in ISO 8601 (e.g. '2025-06-15T10:00:00')"},
             "end":         {"type": "string", "description": "End datetime in ISO 8601"},
             "description": {"type": "string", "description": "Event description"},
             "location":    {"type": "string", "description": "Event location"},
             "attendees":   {"type": "array", "items": {"type": "string"}, "description": "List of attendee email addresses"},
             "calendar_id": {"type": "string", "description": "Calendar ID (default: 'primary')"},
             "timezone":    {"type": "string", "description": "Timezone (e.g. 'America/New_York')"},
         }, "required": ["summary", "start", "end"]}),

    ConnectorAction("update_event", "Update an existing calendar event",
        {"type": "object",
         "properties": {
             "event_id":    {"type": "string", "description": "Calendar event ID"},
             "summary":     {"type": "string", "description": "Updated event title"},
             "start":       {"type": "string", "description": "Updated start datetime"},
             "end":         {"type": "string", "description": "Updated end datetime"},
             "description": {"type": "string", "description": "Updated description"},
             "calendar_id": {"type": "string", "description": "Calendar ID (default: 'primary')"},
         }, "required": ["event_id"]}),

    ConnectorAction("delete_event", "Delete a calendar event",
        {"type": "object",
         "properties": {
             "event_id":    {"type": "string", "description": "Calendar event ID"},
             "calendar_id": {"type": "string", "description": "Calendar ID (default: 'primary')"},
         }, "required": ["event_id"]}),

    ConnectorAction("list_calendars", "List all calendars in the account",
        {"type": "object", "properties": {}, "required": []}),
]


@register_connector("google_calendar")
class GoogleCalendarConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="google_calendar", display_name="Google Calendar",
        description="Create, update, and manage Google Calendar events and meetings.",
        category="google", icon="📅", auth_type="oauth2",
        required_config=["credentials_json"],
        optional_config=["access_token", "default_calendar_id", "default_timezone"],
        actions=_ACTIONS,
    )

    def _client(self):
        return _build_client(self._cfg, _SCOPES)

    def _calendar_id(self, calendar_id: str = "") -> str:
        return calendar_id or self._cfg.get("default_calendar_id", "primary")

    def _timezone(self, timezone: str = "") -> str:
        return timezone or self._cfg.get("default_timezone", "UTC")

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/users/me/calendarList", params={"maxResults": 1})
            r.raise_for_status()

    async def list_events(
        self, calendar_id: str = "", time_min: str = "", time_max: str = "",
        max_results: int = 10, query: str = "",
    ) -> dict:
        params: dict = {
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        if query:
            params["q"] = query

        async with self._client() as c:
            r = await c.get(f"{_BASE}/calendars/{self._calendar_id(calendar_id)}/events",
                            params=params)
            r.raise_for_status()
            items = r.json().get("items", [])
            return {
                "events": [
                    {
                        "id": e.get("id"),
                        "summary": e.get("summary", ""),
                        "start": e.get("start", {}),
                        "end": e.get("end", {}),
                        "location": e.get("location", ""),
                        "description": e.get("description", ""),
                        "attendees": [a.get("email") for a in e.get("attendees", [])],
                        "link": e.get("htmlLink", ""),
                    }
                    for e in items
                ]
            }

    async def get_event(self, event_id: str, calendar_id: str = "") -> dict:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/calendars/{self._calendar_id(calendar_id)}/events/{event_id}")
            r.raise_for_status()
            e = r.json()
            return {
                "id": e.get("id"),
                "summary": e.get("summary", ""),
                "start": e.get("start", {}),
                "end": e.get("end", {}),
                "location": e.get("location", ""),
                "description": e.get("description", ""),
                "attendees": [a.get("email") for a in e.get("attendees", [])],
                "link": e.get("htmlLink", ""),
                "status": e.get("status"),
            }

    async def create_event(
        self, summary: str, start: str, end: str,
        description: str = "", location: str = "",
        attendees: list[str] | None = None,
        calendar_id: str = "", timezone: str = "",
    ) -> dict:
        tz = self._timezone(timezone)
        event: dict = {
            "summary": summary,
            "start": {"dateTime": start, "timeZone": tz},
            "end": {"dateTime": end, "timeZone": tz},
        }
        if description:
            event["description"] = description
        if location:
            event["location"] = location
        if attendees:
            event["attendees"] = [{"email": e} for e in attendees]

        async with self._client() as c:
            r = await c.post(
                f"{_BASE}/calendars/{self._calendar_id(calendar_id)}/events",
                json=event,
                params={"sendUpdates": "all"},
            )
            r.raise_for_status()
            d = r.json()
            return {"ok": True, "event_id": d.get("id"), "link": d.get("htmlLink")}

    async def update_event(
        self, event_id: str, summary: str = "", start: str = "",
        end: str = "", description: str = "", calendar_id: str = "",
    ) -> dict:
        cal = self._calendar_id(calendar_id)
        async with self._client() as c:
            # Fetch existing first (PATCH requires current state)
            existing = await c.get(f"{_BASE}/calendars/{cal}/events/{event_id}")
            existing.raise_for_status()
            event = existing.json()

            if summary:
                event["summary"] = summary
            if description:
                event["description"] = description
            if start:
                event["start"]["dateTime"] = start
            if end:
                event["end"]["dateTime"] = end

            r = await c.put(f"{_BASE}/calendars/{cal}/events/{event_id}", json=event)
            r.raise_for_status()
            return {"ok": True, "event_id": event_id}

    async def delete_event(self, event_id: str, calendar_id: str = "") -> dict:
        async with self._client() as c:
            r = await c.delete(
                f"{_BASE}/calendars/{self._calendar_id(calendar_id)}/events/{event_id}",
                params={"sendUpdates": "all"},
            )
            r.raise_for_status()
            return {"ok": True, "event_id": event_id}

    async def list_calendars(self) -> dict:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/users/me/calendarList")
            r.raise_for_status()
            items = r.json().get("items", [])
            return {
                "calendars": [
                    {
                        "id": cal.get("id"),
                        "summary": cal.get("summary", ""),
                        "primary": cal.get("primary", False),
                        "access_role": cal.get("accessRole", ""),
                        "color": cal.get("backgroundColor", ""),
                    }
                    for cal in items
                ]
            }
