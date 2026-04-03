"""
AWS SQS inbound channel adapter.

Behaviour:
  - Long-polls an SQS queue (WaitTimeSeconds=20) in a background asyncio task
  - Deletes messages after successful dispatch (at-least-once delivery)
  - Supports both standard and FIFO queues
  - Message body must be JSON; envelope fields mapped from message attributes
    or body keys.

Config keys / env vars:
  SQS_QUEUE_URL      — full URL of the queue
  SQS_REGION         — AWS region (default us-east-1)
  SQS_MAX_MESSAGES   — max messages per poll (1–10, default 10)
  SQS_VISIBILITY_TIMEOUT — seconds to hide message while processing (default 60)

AWS credentials are resolved via the standard boto3 chain:
  env vars → ~/.aws/credentials → IAM role.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable, Awaitable

from aiohttp import web

from agentix.watchdog.trigger_normalizer import TriggerEnvelope

log = logging.getLogger(__name__)


class SQSChannel:
    """AWS SQS long-poll inbound channel."""

    def __init__(
        self,
        cfg: dict,
        on_trigger: Callable[[TriggerEnvelope], Awaitable[None]],
        app: web.Application,
    ) -> None:
        self._queue_url: str = cfg.get("sqs_queue_url") or os.environ["SQS_QUEUE_URL"]
        self._region: str = cfg.get("sqs_region") or os.environ.get("SQS_REGION", "us-east-1")
        self._max_messages: int = int(cfg.get("sqs_max_messages") or os.environ.get("SQS_MAX_MESSAGES", "10"))
        self._visibility_timeout: int = int(cfg.get("sqs_visibility_timeout") or os.environ.get("SQS_VISIBILITY_TIMEOUT", "60"))
        self._on_trigger = on_trigger
        self._poll_task: asyncio.Task | None = None
        self._running = False
        self._sqs: Any = None  # boto3 client, created lazily

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        try:
            import boto3
        except ImportError:
            log.error("SQSChannel: boto3 not installed. Run: pip install boto3")
            return
        self._sqs = boto3.client("sqs", region_name=self._region)
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        log.info("SQSChannel polling %s", self._queue_url)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                messages = await loop.run_in_executor(None, self._receive_messages)
                for msg in messages:
                    envelope = _normalise(msg)
                    if envelope:
                        await self._on_trigger(envelope)
                    # Delete on success (at-least-once semantics)
                    await loop.run_in_executor(None, self._delete_message, msg["ReceiptHandle"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("SQSChannel poll error: %s", exc)
                await asyncio.sleep(5)

    def _receive_messages(self) -> list[dict]:
        resp = self._sqs.receive_message(
            QueueUrl=self._queue_url,
            MaxNumberOfMessages=self._max_messages,
            WaitTimeSeconds=20,
            VisibilityTimeout=self._visibility_timeout,
            MessageAttributeNames=["All"],
            AttributeNames=["All"],
        )
        return resp.get("Messages", [])

    def _delete_message(self, receipt_handle: str) -> None:
        self._sqs.delete_message(QueueUrl=self._queue_url, ReceiptHandle=receipt_handle)

    # ------------------------------------------------------------------
    # Outbound: send a message to a queue
    # ------------------------------------------------------------------

    def send_message(self, queue_url: str, body: dict | str, **kwargs) -> dict:
        """Synchronous send — wrap with run_in_executor for async callers."""
        if isinstance(body, dict):
            body = json.dumps(body)
        return self._sqs.send_message(QueueUrl=queue_url, MessageBody=body, **kwargs)


# ---------------------------------------------------------------------------
# Normalisation helper
# ---------------------------------------------------------------------------

def _normalise(msg: dict) -> TriggerEnvelope | None:
    body_raw = msg.get("Body", "{}")
    try:
        body = json.loads(body_raw)
    except Exception:
        body = {"raw_text": body_raw}

    # SNS-wrapped messages have a "Message" key
    if "Message" in body and "TopicArn" in body:
        try:
            inner = json.loads(body["Message"])
        except Exception:
            inner = {"text": body["Message"]}
        body = inner

    attrs = msg.get("MessageAttributes", {})

    def _attr(name: str) -> str:
        return attrs.get(name, {}).get("StringValue", "")

    return TriggerEnvelope(
        channel="sqs",
        event_type=_attr("event_type") or body.get("event_type", "sqs.message"),
        payload={
            "body": body,
            "message_id": msg.get("MessageId", ""),
            "receipt_handle": msg.get("ReceiptHandle", ""),
            "queue_url": "",  # filled by channel if needed
            "attributes": msg.get("Attributes", {}),
        },
        identity={
            "user_id": _attr("user_id") or body.get("user_id", "sqs"),
            "source": _attr("source") or body.get("source", ""),
        },
        raw=msg,
    )
