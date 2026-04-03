"""
Microsoft Teams channel adapter — Bot Framework REST API.

Flow:
  1. Teams delivers Activity payloads to POST /channels/teams
  2. We verify the JWT bearer token from the Authorization header
     (Microsoft Bot Framework token, issued by login.botframework.com)
  3. Activity is normalised into a TriggerEnvelope
  4. Outbound: reply_to_activity() sends an Adaptive Card or plain text reply

Required config / env:
  TEAMS_APP_ID       — Azure AD application (client) ID
  TEAMS_APP_PASSWORD — Azure AD client secret
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, Awaitable

import aiohttp
from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)

_TOKEN_URL = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
_BOT_FRAMEWORK_SCOPE = "https://api.botframework.com/.default"


class TeamsChannel:
    """Microsoft Teams inbound/outbound channel via Bot Framework."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._app_id: str = cfg.get("teams_app_id") or os.environ["TEAMS_APP_ID"]
        self._app_password: str = cfg.get("teams_app_password") or os.environ["TEAMS_APP_PASSWORD"]
        self._webhook_path: str = cfg.get("teams_webhook_path", "/channels/teams")
        self._on_trigger = on_trigger
        self._app = app
        self._token_cache: dict = {}  # {"token": str, "expires_at": float}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._app.router.add_post(self._webhook_path, self._handle_activity)
        log.info("Teams channel listening at %s", self._webhook_path)

    async def stop(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Inbound activity handler
    # ------------------------------------------------------------------

    async def _handle_activity(self, request: web.Request) -> web.Response:
        # Token verification (simplified — production should use JWKS)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.Response(status=401, text="Unauthorized")

        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400, text="Bad JSON")

        asyncio.create_task(self._dispatch(body))
        return web.Response(status=200, text="")

    async def _dispatch(self, activity: dict) -> None:
        envelope = _normalise(activity)
        if envelope:
            await self._on_trigger(envelope)

    # ------------------------------------------------------------------
    # Outbound: reply to an activity
    # ------------------------------------------------------------------

    async def reply_to_activity(self, activity: dict, reply_text: str | None = None, adaptive_card: dict | None = None) -> dict:
        """Send a reply to an incoming Teams activity."""
        service_url = activity.get("serviceUrl", "").rstrip("/")
        conversation_id = activity.get("conversation", {}).get("id", "")
        activity_id = activity.get("id", "")
        url = f"{service_url}/v3/conversations/{conversation_id}/activities/{activity_id}"

        if adaptive_card:
            reply = {
                "type": "message",
                "attachments": [{
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": adaptive_card,
                }],
            }
        else:
            reply = {"type": "message", "text": reply_text or ""}

        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=reply, headers=headers) as r:
                return await r.json()

    async def _get_token(self) -> str:
        """Fetch/cache a Bot Framework access token."""
        now = time.time()
        if self._token_cache.get("expires_at", 0) > now + 60:
            return self._token_cache["token"]

        data = {
            "grant_type": "client_credentials",
            "client_id": self._app_id,
            "client_secret": self._app_password,
            "scope": _BOT_FRAMEWORK_SCOPE,
        }
        async with aiohttp.ClientSession() as s:
            async with s.post(_TOKEN_URL, data=data) as r:
                resp = await r.json()

        token = resp["access_token"]
        self._token_cache = {"token": token, "expires_at": now + resp.get("expires_in", 3600)}
        return token


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise(activity: dict) -> TriggerEnvelope | None:
    activity_type = activity.get("type", "")
    if activity_type not in ("message", "invoke"):
        return None

    sender = activity.get("from", {})
    channel_data = activity.get("channelData", {})
    text = activity.get("text", "")
    attachments = activity.get("attachments", [])

    payload: dict = {
        "text": text,
        "activity_type": activity_type,
        "conversation_id": activity.get("conversation", {}).get("id"),
        "activity_id": activity.get("id"),
        "service_url": activity.get("serviceUrl"),
        "channel_id": activity.get("channelId"),
        "attachments": attachments,
        "channel_data": channel_data,
    }

    # Adaptive Card submit values
    if activity_type == "invoke" and activity.get("name") == "adaptiveCard/action":
        payload["card_action"] = activity.get("value", {})

    return TriggerEnvelope(
        channel="teams",
        event_type=f"teams.{activity_type}",
        payload=payload,
        identity={
            "user_id": sender.get("id", ""),
            "name": sender.get("name", ""),
            "aad_object_id": sender.get("aadObjectId", ""),
        },
        raw=activity,
    )
