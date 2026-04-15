"""
Telegram Bot API channel adapter.

Supports:
 - Webhook mode (production): registers /setWebhook with Telegram and handles
   incoming updates via an aiohttp POST route.
 - Polling mode (dev): long-polls getUpdates when no PUBLIC_URL is configured.

Each incoming message/command is normalised into a TriggerEnvelope and handed
to the shared on_trigger callback.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from typing import Callable, Awaitable

import aiohttp
from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramChannel:
    """Telegram Bot API inbound channel."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._token: str = cfg.get("telegram_bot_token") or os.environ["TELEGRAM_BOT_TOKEN"]
        self._secret_token: str = cfg.get("telegram_webhook_secret", "")
        self._public_url: str = cfg.get("public_url", "").rstrip("/")
        self._webhook_path: str = cfg.get("telegram_webhook_path", "/channels/telegram")
        self._default_agent_id: str = cfg.get("default_agent_id", "")
        self._on_trigger = on_trigger
        self._app = app
        self._base = _TELEGRAM_API.format(token=self._token)
        self._poll_task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        if self._public_url:
            await self._register_webhook()
            self._app.router.add_post(self._webhook_path, self._handle_webhook)
            log.info("Telegram webhook registered at %s%s", self._public_url, self._webhook_path)
        else:
            log.info("No PUBLIC_URL — Telegram using long-polling")
            await self._delete_webhook()
            self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Webhook path
    # ------------------------------------------------------------------

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        # Verify optional X-Telegram-Bot-Api-Secret-Token header
        if self._secret_token:
            provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not hmac.compare_digest(provided, self._secret_token):
                return web.Response(status=403, text="Forbidden")

        try:
            update = await request.json()
        except Exception:
            return web.Response(status=400, text="Bad JSON")

        asyncio.create_task(self._dispatch(update))
        return web.Response(status=200, text="OK")

    async def _register_webhook(self) -> None:
        url = f"{self._base}/setWebhook"
        params: dict = {"url": f"{self._public_url}{self._webhook_path}"}
        if self._secret_token:
            params["secret_token"] = self._secret_token
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=params) as r:
                body = await r.json()
                if not body.get("ok"):
                    log.warning("Telegram setWebhook failed: %s", body)

    async def _delete_webhook(self) -> None:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self._base}/deleteWebhook") as r:
                await r.read()

    # ------------------------------------------------------------------
    # Polling path
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        offset = 0
        backoff = 5.0
        consecutive_errors = 0
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    # Build as list of 2-tuples so aiohttp can repeat the
                    # allowed_updates key — this also satisfies mypy's type stubs.
                    params_list = [
                        ("timeout", "30"),
                        ("offset", str(offset)),
                        ("allowed_updates", "message"),
                        ("allowed_updates", "callback_query"),
                    ]
                    async with session.get(f"{self._base}/getUpdates", params=params_list, timeout=aiohttp.ClientTimeout(total=40)) as r:
                        data = await r.json()
                    if data.get("ok"):
                        if consecutive_errors > 0:
                            log.info("Telegram polling recovered after %d error(s)", consecutive_errors)
                        consecutive_errors = 0
                        backoff = 5.0
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            await self._dispatch(update)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    consecutive_errors += 1
                    # Log first error and every 12th after (once per minute at 5s backoff)
                    if consecutive_errors == 1:
                        log.warning("Telegram poll error: %s", exc)
                    elif consecutive_errors % 12 == 0:
                        log.warning("Telegram poll still failing (%d errors): %s", consecutive_errors, exc)
                    # Exponential backoff capped at 60s
                    await asyncio.sleep(min(backoff, 60.0))
                    backoff = min(backoff * 1.5, 60.0)

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    async def _dispatch(self, update: dict) -> None:
        envelope = _normalise(update)
        if envelope:
            from agentix.watchdog.channels.router import AgentRouter
            router = AgentRouter(self._default_agent_id)
            text = envelope.payload.get("text", "")
            envelope.payload["_agent_id"] = router.resolve(text)
            envelope.payload["text"] = router.strip_prefix(text)
            await self._on_trigger(envelope)

    # ------------------------------------------------------------------
    # Outbound helper (send text reply)
    # ------------------------------------------------------------------

    async def send_message(self, chat_id: int | str, text: str, **kwargs) -> dict:
        payload = {"chat_id": chat_id, "text": text, **kwargs}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{self._base}/sendMessage", json=payload) as r:
                return await r.json()


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise(update: dict) -> TriggerEnvelope | None:
    """Convert a Telegram Update dict into a TriggerEnvelope."""
    msg = update.get("message") or update.get("edited_message")
    cb = update.get("callback_query")

    if msg:
        chat = msg.get("chat", {})
        sender = msg.get("from", {})
        text = msg.get("text", "")
        return TriggerEnvelope(
            channel="telegram",
            event_type="message",
            payload={
                "text": text,
                "chat_id": chat.get("id"),
                "chat_type": chat.get("type"),
                "message_id": msg.get("message_id"),
                "update_id": update.get("update_id"),
            },
            identity={
                "user_id": str(sender.get("id", "")),
                "username": sender.get("username", ""),
                "first_name": sender.get("first_name", ""),
            },
            raw=update,
        )

    if cb:
        sender = cb.get("from", {})
        return TriggerEnvelope(
            channel="telegram",
            event_type="callback_query",
            payload={
                "data": cb.get("data", ""),
                "callback_query_id": cb.get("id"),
                "message": cb.get("message", {}),
            },
            identity={
                "user_id": str(sender.get("id", "")),
                "username": sender.get("username", ""),
            },
            raw=update,
        )

    return None
