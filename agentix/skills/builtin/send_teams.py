"""
Built-in Skill: send_teams
Posts messages to Microsoft Teams via an Incoming Webhook URL.

This uses Teams Incoming Webhooks (simpler than Bot Framework — no auth token
needed, just a webhook URL from the Teams channel connector settings).

Required env vars:
  TEAMS_WEBHOOK_URL — the Incoming Webhook URL from Teams channel settings
"""
from __future__ import annotations

import os

import httpx

INSTRUCTIONS = """
## Microsoft Teams Skill
You can post messages to Microsoft Teams using the `send_teams` tool.

- Use `send_teams` to deliver reports, alerts, or summaries to a Teams channel.
- Supports basic Markdown in the `message` field.
- Provide an optional `title` to display as a card header.
- The webhook URL is read from TEAMS_WEBHOOK_URL env var.
""".strip()


def _send_teams(message: str, title: str = "") -> dict:
    """Post a message to a Teams channel via Incoming Webhook."""
    webhook_url = os.environ.get("TEAMS_WEBHOOK_URL", "")
    if not webhook_url:
        return {"ok": False, "error": "TEAMS_WEBHOOK_URL env var not set"}

    # MessageCard format (works with all Teams versions)
    card: dict = {
        "@type": "MessageCard",
        "@context": "https://schema.org/extensions",
        "themeColor": "0078D4",
        "summary": title or message[:80],
    }
    if title:
        card["title"] = title
    card["text"] = message

    try:
        resp = httpx.post(webhook_url, json=card, timeout=15)
        if resp.status_code == 200 and resp.text == "1":
            return {"ok": True}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = {
    "send_teams": _send_teams,
}

TOOL_SCHEMAS = [
    {
        "name": "send_teams",
        "description": (
            "Post a message to a Microsoft Teams channel via an Incoming Webhook. "
            "Reads TEAMS_WEBHOOK_URL from env. Supports Markdown in the message body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message body (Markdown supported)",
                },
                "title": {
                    "type": "string",
                    "description": "Optional card title displayed above the message",
                    "default": "",
                },
            },
            "required": ["message"],
        },
    },
]
