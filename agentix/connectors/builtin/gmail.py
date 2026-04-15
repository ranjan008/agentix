"""Gmail connector — send, search, and manage emails via the Gmail API."""
from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from agentix.connectors.base import BaseConnector, ConnectorAction, ConnectorMeta
from agentix.connectors.registry import register_connector
from agentix.connectors.builtin._google_auth import _build_client

_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

_ACTIONS = [
    ConnectorAction("send_email", "Send an email via Gmail",
        {"type": "object",
         "properties": {
             "to":      {"type": "string", "description": "Recipient email address"},
             "subject": {"type": "string", "description": "Email subject line"},
             "body":    {"type": "string", "description": "Email body (plain text or HTML)"},
             "cc":      {"type": "string", "description": "CC email addresses (comma-separated)"},
             "html":    {"type": "boolean", "description": "Send body as HTML (default false)"},
         }, "required": ["to", "subject", "body"]}),

    ConnectorAction("list_emails", "List recent emails in the inbox",
        {"type": "object",
         "properties": {
             "query":     {"type": "string", "description": "Gmail search query (e.g. 'from:user@example.com is:unread')"},
             "max_results": {"type": "integer", "description": "Maximum number of messages to return (default 10)"},
             "label":     {"type": "string", "description": "Filter by label (e.g. INBOX, SENT, UNREAD)"},
         }, "required": []}),

    ConnectorAction("get_email", "Get the full content of an email by message ID",
        {"type": "object",
         "properties": {
             "message_id": {"type": "string", "description": "Gmail message ID"},
         }, "required": ["message_id"]}),

    ConnectorAction("search_emails", "Search emails using Gmail query syntax",
        {"type": "object",
         "properties": {
             "query":       {"type": "string", "description": "Gmail search query"},
             "max_results": {"type": "integer", "description": "Max results (default 20)"},
         }, "required": ["query"]}),

    ConnectorAction("manage_labels", "Add or remove labels from a message",
        {"type": "object",
         "properties": {
             "message_id":    {"type": "string", "description": "Gmail message ID"},
             "add_labels":    {"type": "array", "items": {"type": "string"}, "description": "Labels to add (e.g. ['STARRED', 'UNREAD'])"},
             "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to remove"},
         }, "required": ["message_id"]}),
]


@register_connector("gmail")
class GmailConnector(BaseConnector):
    meta = ConnectorMeta(
        type_name="gmail", display_name="Gmail",
        description="Send and manage emails via Google Gmail API.",
        category="google", icon="📧", auth_type="oauth2",
        required_config=["credentials_json"],
        optional_config=["access_token", "user_email"],
        actions=_ACTIONS,
    )

    def _client(self):
        return _build_client(self._cfg, _SCOPES)

    async def connect(self) -> None:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/profile")
            r.raise_for_status()

    async def send_email(
        self, to: str, subject: str, body: str,
        cc: str = "", html: bool = False,
    ) -> dict:
        mime = MIMEMultipart("alternative") if html else MIMEText(body, "plain")
        if html:
            mime_plain = MIMEText(body, "plain")
            mime_html = MIMEText(body, "html")
            mime.attach(mime_plain)
            mime.attach(mime_html)

        if isinstance(mime, MIMEMultipart):
            mime["to"] = to
            mime["subject"] = subject
            if cc:
                mime["cc"] = cc
        else:
            mime["to"] = to
            mime["subject"] = subject
            if cc:
                mime["cc"] = cc

        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        async with self._client() as c:
            r = await c.post(f"{_BASE}/messages/send", json={"raw": raw})
            r.raise_for_status()
            return {"ok": True, "message_id": r.json().get("id")}

    async def list_emails(
        self, query: str = "", max_results: int = 10, label: str = "INBOX"
    ) -> dict:
        params: dict = {"maxResults": max_results}
        q_parts = []
        if query:
            q_parts.append(query)
        if label and label not in ("", "ALL"):
            params["labelIds"] = label
        if q_parts:
            params["q"] = " ".join(q_parts)

        async with self._client() as c:
            r = await c.get(f"{_BASE}/messages", params=params)
            r.raise_for_status()
            data = r.json()
            messages = data.get("messages", [])

            # Fetch snippet for each
            result = []
            for m in messages[:max_results]:
                mr = await c.get(f"{_BASE}/messages/{m['id']}",
                                 params={"format": "metadata",
                                         "metadataHeaders": ["Subject", "From", "Date"]})
                if mr.is_success:
                    md = mr.json()
                    headers = {h["name"]: h["value"]
                               for h in md.get("payload", {}).get("headers", [])}
                    result.append({
                        "id": m["id"],
                        "subject": headers.get("Subject", ""),
                        "from": headers.get("From", ""),
                        "date": headers.get("Date", ""),
                        "snippet": md.get("snippet", ""),
                    })
            return {"messages": result, "total": data.get("resultSizeEstimate", len(result))}

    async def get_email(self, message_id: str) -> dict:
        async with self._client() as c:
            r = await c.get(f"{_BASE}/messages/{message_id}", params={"format": "full"})
            r.raise_for_status()
            data = r.json()
            headers = {h["name"]: h["value"]
                       for h in data.get("payload", {}).get("headers", [])}

            # Extract body
            body = ""
            parts = data.get("payload", {}).get("parts", [])
            if parts:
                for part in parts:
                    if part.get("mimeType") == "text/plain":
                        raw = part.get("body", {}).get("data", "")
                        body = base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")
                        break
            else:
                raw = data.get("payload", {}).get("body", {}).get("data", "")
                if raw:
                    body = base64.urlsafe_b64decode(raw + "==").decode("utf-8", errors="replace")

            return {
                "id": message_id,
                "subject": headers.get("Subject", ""),
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "date": headers.get("Date", ""),
                "snippet": data.get("snippet", ""),
                "body": body,
                "labels": data.get("labelIds", []),
            }

    async def search_emails(self, query: str, max_results: int = 20) -> dict:
        return await self.list_emails(query=query, max_results=max_results, label="")

    async def manage_labels(
        self, message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict:
        payload: dict = {}
        if add_labels:
            payload["addLabelIds"] = add_labels
        if remove_labels:
            payload["removeLabelIds"] = remove_labels
        async with self._client() as c:
            r = await c.post(f"{_BASE}/messages/{message_id}/modify", json=payload)
            r.raise_for_status()
            return {"ok": True, "message_id": message_id, "labels": r.json().get("labelIds", [])}
