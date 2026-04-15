"""
Built-in Skill: send_whatsapp
Sends messages via the Meta WhatsApp Cloud API — same credentials as WhatsAppChannel.

Required env vars:
  WHATSAPP_ACCESS_TOKEN    — permanent system-user token from Meta Business
  WHATSAPP_PHONE_NUMBER_ID — phone number ID from Meta Business dashboard
  WHATSAPP_DEFAULT_TO      — fallback recipient number in E.164 format (e.g. 919876543210)
"""
from __future__ import annotations

import os

import httpx

_GRAPH_API = "https://graph.facebook.com/v18.0"

INSTRUCTIONS = """
## WhatsApp Skill
You can send WhatsApp messages using the `send_whatsapp` tool.

- Use `send_whatsapp` to deliver reports or alerts via WhatsApp.
- The `to` field must be an E.164 number without '+' (e.g. 919876543210).
- Plain text only — WhatsApp does not support Markdown in text messages.
- Keep messages concise; long messages may be split into multiple calls.
- Reads WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID from env.
""".strip()


def _send_whatsapp(message: str, to: str = "") -> dict:
    """Send a WhatsApp text message via Meta Cloud API."""
    access_token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    default_to = os.environ.get("WHATSAPP_DEFAULT_TO", "")
    recipient = to or default_to

    if not access_token:
        return {"ok": False, "error": "WHATSAPP_ACCESS_TOKEN env var not set"}
    if not phone_number_id:
        return {"ok": False, "error": "WHATSAPP_PHONE_NUMBER_ID env var not set"}
    if not recipient:
        return {"ok": False, "error": "No recipient: provide `to` or set WHATSAPP_DEFAULT_TO"}

    url = f"{_GRAPH_API}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": message},
    }
    try:
        resp = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        data = resp.json()
        if "messages" in data:
            return {"ok": True, "message_id": data["messages"][0].get("id")}
        return {"ok": False, "error": data.get("error", {}).get("message", str(data))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


TOOLS = {
    "send_whatsapp": _send_whatsapp,
}

TOOL_SCHEMAS = [
    {
        "name": "send_whatsapp",
        "description": (
            "Send a WhatsApp text message via Meta Cloud API. "
            "Recipient must be E.164 format without '+' (e.g. 919876543210). "
            "Defaults to WHATSAPP_DEFAULT_TO if `to` is omitted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message text to send (plain text only)",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient phone in E.164 without '+' (e.g. 919876543210). Uses WHATSAPP_DEFAULT_TO if omitted.",
                    "default": "",
                },
            },
            "required": ["message"],
        },
    },
]
