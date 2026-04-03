"""
Durable Trigger Queue — Redis List-backed queue shared across watchdog replicas.

Replaces the direct in-process spawn for environments where:
  - Multiple watchdog replicas run behind a load balancer
  - Triggers must survive watchdog restarts (at-least-once delivery)
  - Deduplication via idempotency key is required

Flow:
  Channel adapter → enqueue(envelope)   [any replica]
  Worker loop     → dequeue() → spawn  [any replica, BLPOP for blocking pop]

Deduplication:
  Redis SET key=agentix:dedup:<idempotency_key> NX EX 86400
  If key already exists → trigger is a duplicate, skip silently.
"""
from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

_QUEUE_KEY = "agentix:trigger-queue"
_DEDUP_PREFIX = "agentix:dedup:"
_DEDUP_TTL = 86_400  # 24 hours


class TriggerQueue:
    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self.redis_url = redis_url
        self._redis = None

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis
                self._redis = redis.from_url(self.redis_url, decode_responses=True)
            except ImportError:
                raise ImportError("Trigger queue requires 'redis': pip install redis")
        return self._redis

    def enqueue(self, envelope: dict) -> bool:
        """
        Push envelope onto the queue. Returns False if duplicate (idempotency).
        """
        r = self._get_redis()
        idem_key = envelope.get("idempotency_key", envelope["id"])
        dedup_key = f"{_DEDUP_PREFIX}{idem_key}"

        # Atomic dedup check + mark
        is_new = r.set(dedup_key, "1", nx=True, ex=_DEDUP_TTL)
        if not is_new:
            logger.debug("Duplicate trigger dropped: %s", idem_key)
            return False

        r.rpush(_QUEUE_KEY, json.dumps(envelope))
        return True

    def dequeue(self, timeout_sec: int = 5) -> dict | None:
        """Blocking pop. Returns None on timeout."""
        r = self._get_redis()
        result = r.blpop(_QUEUE_KEY, timeout=timeout_sec)
        if result is None:
            return None
        _, raw = result
        return json.loads(raw)

    def depth(self) -> int:
        return self._get_redis().llen(_QUEUE_KEY)

    async def worker_loop(self, on_trigger, stop_event: asyncio.Event) -> None:
        """Async worker: pops triggers and fires on_trigger callback."""
        logger.info("Trigger queue worker started")
        loop = asyncio.get_event_loop()
        while not stop_event.is_set():
            try:
                envelope = await loop.run_in_executor(None, lambda: self.dequeue(timeout_sec=2))
                if envelope:
                    await on_trigger(envelope)
            except Exception as exc:
                logger.exception("Trigger queue worker error: %s", exc)
