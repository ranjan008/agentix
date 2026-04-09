"""
Built-in Skill: send_telegram
Sends messages to a Telegram chat via the Bot API.

Required env vars:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — target chat / channel ID (can be prefixed with -)
"""
from __future__ import annotations

import os

import httpx

INSTRUCTIONS = """
## Telegram Skill
You can send messages to Telegram using the `send_telegram` tool.

- Use `send_telegram` to deliver reports, alerts, or summaries.
- Telegram messages support Markdown (use *bold*, _italic_, `code`).
- Maximum message length is 4096 characters — split long messages into multiple calls.
- Always send your final output via `send_telegram`.
""".strip()


def _send_telegram(message: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to the configured Telegram chat."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN env var not set"}
    if not chat_id:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID env var not set"}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
    }
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            return {"ok": True, "message_id": data["result"]["message_id"]}
        return {"ok": False, "error": data.get("description", "Unknown error")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = {
    "send_telegram": _send_telegram,
}

TOOL_SCHEMAS = [
    {
        "name": "send_telegram",
        "description": (
            "Send a text message to the configured Telegram chat. "
            "Supports Markdown formatting. Max 4096 chars per message — "
            "call multiple times for longer content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text to send (Markdown supported)",
                },
                "parse_mode": {
                    "type": "string",
                    "description": "Telegram parse mode: 'Markdown' or 'HTML' (default: Markdown)",
                    "default": "Markdown",
                },
            },
            "required": ["message"],
        },
    },
]
