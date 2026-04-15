"""
Built-in Skill: send_slack
Posts messages to a Slack channel via the Web API.

Required env vars:
  SLACK_BOT_TOKEN      — xoxb-... from Slack app OAuth page
  SLACK_DEFAULT_CHANNEL — e.g. #general or C0123456789
"""
from __future__ import annotations

import os

import httpx

INSTRUCTIONS = """
## Slack Skill
You can post messages to Slack using the `send_slack` tool.

- Use `send_slack` to deliver reports, alerts, or summaries to a Slack channel.
- Slack supports mrkdwn formatting: *bold*, _italic_, `code`, ```code block```.
- Specify a `channel` to override the default (e.g. "#alerts", "C0123456789").
- For long content, call `send_slack` multiple times with separate sections.
""".strip()


def _send_slack(message: str, channel: str = "", username: str = "Agentix") -> dict:
    """Post a message to a Slack channel."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    default_channel = os.environ.get("SLACK_DEFAULT_CHANNEL", "")
    target = channel or default_channel

    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN env var not set"}
    if not target:
        return {"ok": False, "error": "No channel specified and SLACK_DEFAULT_CHANNEL not set"}

    payload = {
        "channel": target,
        "text": message,
        "username": username,
        "mrkdwn": True,
    }
    try:
        resp = httpx.post(
            "https://slack.com/api/chat.postMessage",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = resp.json()
        if data.get("ok"):
            return {"ok": True, "ts": data.get("ts"), "channel": data.get("channel")}
        return {"ok": False, "error": data.get("error", "Unknown error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = {
    "send_slack": _send_slack,
}

TOOL_SCHEMAS = [
    {
        "name": "send_slack",
        "description": (
            "Post a message to a Slack channel. "
            "Supports mrkdwn formatting. Use SLACK_DEFAULT_CHANNEL env var or specify channel."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text (mrkdwn supported)",
                },
                "channel": {
                    "type": "string",
                    "description": "Slack channel name or ID (e.g. '#general'). Uses SLACK_DEFAULT_CHANNEL if omitted.",
                    "default": "",
                },
                "username": {
                    "type": "string",
                    "description": "Display name for the bot message (default: Agentix)",
                    "default": "Agentix",
                },
            },
            "required": ["message"],
        },
    },
]
