"""
WhatsApp channel adapter — Meta Cloud API (v18+).

Supports:
 - Webhook verification (GET) — responds to hub.challenge
 - Inbound message handling (POST) — text, interactive button replies, audio,
   image, document
 - Outbound helpers: send_text(), send_template(), send_interactive()

Set in config / env:
  WHATSAPP_ACCESS_TOKEN  — permanent system-user token
  WHATSAPP_PHONE_NUMBER_ID — phone-number ID from Meta Business dashboard
  WHATSAPP_VERIFY_TOKEN  — arbitrary string for webhook verification
  WHATSAPP_APP_SECRET    — (optional) used to verify X-Hub-Signature-256
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Callable, Awaitable

import aiohttp
from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)

_GRAPH_API = "https://graph.facebook.com/v18.0"


class WhatsAppChannel:
    """Meta Cloud API inbound channel."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._access_token: str = cfg.get("whatsapp_access_token") or os.environ["WHATSAPP_ACCESS_TOKEN"]
        self._phone_number_id: str = cfg.get("whatsapp_phone_number_id") or os.environ["WHATSAPP_PHONE_NUMBER_ID"]
        self._verify_token: str = cfg.get("whatsapp_verify_token") or os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
        self._app_secret: str = cfg.get("whatsapp_app_secret") or os.environ.get("WHATSAPP_APP_SECRET", "")
        self._webhook_path: str = cfg.get("whatsapp_webhook_path", "/channels/whatsapp")
        self._on_trigger = on_trigger
        self._app = app

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._app.router.add_get(self._webhook_path, self._handle_verify)
        self._app.router.add_post(self._webhook_path, self._handle_event)
        log.info("WhatsApp channel listening at %s", self._webhook_path)

    async def stop(self) -> None:
        pass  # stateless HTTP handler — nothing to clean up

    # ------------------------------------------------------------------
    # Webhook verification (GET)
    # ------------------------------------------------------------------

    async def _handle_verify(self, request: web.Request) -> web.Response:
        mode = request.rel_url.query.get("hub.mode")
        token = request.rel_url.query.get("hub.verify_token")
        challenge = request.rel_url.query.get("hub.challenge", "")
        if mode == "subscribe" and token == self._verify_token:
            return web.Response(text=challenge)
        return web.Response(status=403, text="Forbidden")

    # ------------------------------------------------------------------
    # Inbound events (POST)
    # ------------------------------------------------------------------

    async def _handle_event(self, request: web.Request) -> web.Response:
        body_bytes = await request.read()

        # Optional HMAC verification
        if self._app_secret:
            sig = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body_bytes)
        except Exception:
            return web.Response(status=400, text="Bad JSON")

        import asyncio
        asyncio.create_task(self._dispatch(data))
        return web.Response(status=200, text="OK")

    async def _dispatch(self, data: dict) -> None:
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    envelope = _normalise(msg, value)
                    if envelope:
                        await self._on_trigger(envelope)
                # Status updates (delivered / read) — skip
                for _status in value.get("statuses", []):
                    pass

    # ------------------------------------------------------------------
    # Outbound helpers
    # ------------------------------------------------------------------

    async def send_text(self, to: str, body: str) -> dict:
        return await self._send({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        })

    async def send_template(self, to: str, template_name: str, language_code: str = "en_US", components: list | None = None) -> dict:
        payload: dict = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        if components:
            payload["template"]["components"] = components
        return await self._send(payload)

    async def send_interactive(self, to: str, interactive: dict) -> dict:
        return await self._send({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        })

    async def _send(self, payload: dict) -> dict:
        url = f"{_GRAPH_API}/{self._phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers) as r:
                return await r.json()


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise(msg: dict, value: dict) -> TriggerEnvelope | None:
    contacts = {c["wa_id"]: c.get("profile", {}) for c in value.get("contacts", [])}
    sender_id = msg.get("from", "")
    profile = contacts.get(sender_id, {})
    msg_type = msg.get("type", "text")

    payload: dict = {"message_id": msg.get("id"), "type": msg_type, "timestamp": msg.get("timestamp")}

    if msg_type == "text":
        payload["text"] = msg.get("text", {}).get("body", "")
    elif msg_type == "interactive":
        inter = msg.get("interactive", {})
        inter_type = inter.get("type")
        if inter_type == "button_reply":
            payload["button_id"] = inter["button_reply"]["id"]
            payload["button_title"] = inter["button_reply"]["title"]
        elif inter_type == "list_reply":
            payload["list_id"] = inter["list_reply"]["id"]
            payload["list_title"] = inter["list_reply"]["title"]
        payload["interactive_type"] = inter_type
    elif msg_type in ("image", "audio", "document", "video"):
        payload["media"] = msg.get(msg_type, {})
    else:
        payload["raw_message"] = msg

    return TriggerEnvelope(
        channel="whatsapp",
        event_type=f"message.{msg_type}",
        payload=payload,
        identity={"user_id": sender_id, "name": profile.get("name", "")},
        raw=msg,
    )
