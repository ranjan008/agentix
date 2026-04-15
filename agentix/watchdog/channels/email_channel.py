"""
Email inbound/outbound channel adapter.

Inbound  — IMAP IDLE polling (asyncio-friendly via run_in_executor)
Outbound — SMTP reply via smtplib (reuses agentix/skills/builtin/email_composer)

Config keys / env vars:
  EMAIL_IMAP_HOST, EMAIL_IMAP_PORT (default 993), EMAIL_IMAP_USE_SSL (true)
  EMAIL_IMAP_USER, EMAIL_IMAP_PASSWORD
  EMAIL_IMAP_MAILBOX  — mailbox to monitor (default INBOX)
  EMAIL_POLL_INTERVAL — seconds between polls (default 30)

  EMAIL_SMTP_HOST, EMAIL_SMTP_PORT (default 587)
  EMAIL_SMTP_USER, EMAIL_SMTP_PASSWORD
  EMAIL_SMTP_USE_TLS (default true)
"""
from __future__ import annotations

import asyncio
import email as email_lib
import email.policy
import imaplib
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Awaitable

from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class EmailChannel:
    """IMAP polling inbound + SMTP outbound email channel."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._imap_host = cfg.get("email_imap_host") or _env("EMAIL_IMAP_HOST")
        self._imap_port = int(cfg.get("email_imap_port") or _env("EMAIL_IMAP_PORT", "993"))
        self._imap_ssl = str(cfg.get("email_imap_use_ssl", _env("EMAIL_IMAP_USE_SSL", "true"))).lower() == "true"
        self._imap_user = cfg.get("email_imap_user") or _env("EMAIL_IMAP_USER")
        self._imap_pass = cfg.get("email_imap_password") or _env("EMAIL_IMAP_PASSWORD")
        self._mailbox = cfg.get("email_imap_mailbox") or _env("EMAIL_IMAP_MAILBOX", "INBOX")
        self._poll_interval = int(cfg.get("email_poll_interval") or _env("EMAIL_POLL_INTERVAL", "30"))

        self._smtp_host = cfg.get("email_smtp_host") or _env("EMAIL_SMTP_HOST")
        self._smtp_port = int(cfg.get("email_smtp_port") or _env("EMAIL_SMTP_PORT", "587"))
        self._smtp_user = cfg.get("email_smtp_user") or _env("EMAIL_SMTP_USER")
        self._smtp_pass = cfg.get("email_smtp_password") or _env("EMAIL_SMTP_PASSWORD")
        self._smtp_tls = str(cfg.get("email_smtp_use_tls", _env("EMAIL_SMTP_USE_TLS", "true"))).lower() == "true"

        self._default_agent_id = cfg.get("default_agent_id", "")
        self._on_trigger = on_trigger
        self._seen_uids: set[bytes] = set()
        self._poll_task: asyncio.Task | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if not self._imap_host:
            log.warning("EmailChannel: EMAIL_IMAP_HOST not set, skipping")
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("EmailChannel polling %s@%s/%s every %ds", self._imap_user, self._imap_host, self._mailbox, self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # IMAP poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                messages = await loop.run_in_executor(None, self._fetch_unseen)
                for msg_dict in messages:
                    envelope = _normalise(msg_dict)
                    if envelope:
                        from agentix.watchdog.channels.router import AgentRouter
                        router = AgentRouter(self._default_agent_id)
                        # Route by subject first, fall back to body
                        routing_text = envelope.payload.get("subject", "") or envelope.payload.get("body", "")
                        envelope.payload["_agent_id"] = router.resolve(routing_text)
                        await self._on_trigger(envelope)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("EmailChannel poll error: %s", exc)
            await asyncio.sleep(self._poll_interval)

    def _fetch_unseen(self) -> list[dict]:
        """Blocking IMAP fetch — runs in executor thread."""
        cls = imaplib.IMAP4_SSL if self._imap_ssl else imaplib.IMAP4
        conn = cls(self._imap_host, self._imap_port)
        try:
            conn.login(self._imap_user, self._imap_pass)
            conn.select(self._mailbox, readonly=False)
            _, data = conn.search(None, "UNSEEN")
            uids = data[0].split()
            results: list[dict] = []
            for uid in uids:
                if uid in self._seen_uids:
                    continue
                _, msg_data = conn.fetch(uid, "(RFC822)")
                raw = msg_data[0][1]  # type: ignore[index]
                parsed = email_lib.message_from_bytes(raw, policy=email_lib.policy.default)  # type: ignore[arg-type]
                results.append(_parse_email(uid.decode(), parsed))
                self._seen_uids.add(uid)
                # Mark as seen
                conn.store(uid, "+FLAGS", "\\Seen")
            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Outbound: SMTP reply
    # ------------------------------------------------------------------

    def send_reply(self, to: str, subject: str, body: str, reply_to_msg_id: str | None = None) -> None:
        """Synchronous SMTP send (call from executor if needed)."""
        msg = MIMEMultipart("alternative")
        msg["From"] = self._smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        if reply_to_msg_id:
            msg["In-Reply-To"] = reply_to_msg_id
            msg["References"] = reply_to_msg_id
        msg.attach(MIMEText(body, "plain"))

        if self._smtp_tls:
            srv = smtplib.SMTP(self._smtp_host, self._smtp_port)
            srv.starttls()
        else:
            srv = smtplib.SMTP_SSL(self._smtp_host, self._smtp_port)

        try:
            srv.login(self._smtp_user, self._smtp_pass)
            srv.sendmail(self._smtp_user, [to], msg.as_string())
        finally:
            srv.quit()

    async def async_send_reply(self, to: str, subject: str, body: str, reply_to_msg_id: str | None = None) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.send_reply, to, subject, body, reply_to_msg_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_email(uid: str, msg) -> dict:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not part.get("Content-Disposition"):
                body = part.get_content()
                break
    else:
        body = msg.get_content()

    return {
        "uid": uid,
        "message_id": msg.get("Message-ID", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "subject": msg.get("Subject", ""),
        "date": msg.get("Date", ""),
        "body": body.strip() if isinstance(body, str) else "",
        "raw": str(msg),
    }


def _normalise(msg_dict: dict) -> TriggerEnvelope:
    sender = msg_dict.get("from", "")
    return TriggerEnvelope(
        channel="email",
        event_type="email.received",
        payload={
            "subject": msg_dict.get("subject", ""),
            "body": msg_dict.get("body", ""),
            "message_id": msg_dict.get("message_id", ""),
            "uid": msg_dict.get("uid", ""),
            "date": msg_dict.get("date", ""),
            "to": msg_dict.get("to", ""),
        },
        identity={"user_id": sender, "email": sender},
        raw=msg_dict,
    )
