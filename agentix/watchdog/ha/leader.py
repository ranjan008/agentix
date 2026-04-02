"""
Leader Election — Redis SETNX-based distributed lock.

Ensures only one watchdog replica runs the scheduler at a time.
All replicas can process triggers; only the leader runs cron/DAG scheduling.

Algorithm:
  1. Try SET watchdog:leader <instance_id> NX PX <ttl_ms>
  2. If acquired → we are leader, start renewing every ttl/3 seconds
  3. If not acquired → follower mode, poll until leader key expires
  4. On SIGTERM: release lock and stop renewal
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid

logger = logging.getLogger(__name__)

_LEADER_KEY = "agentix:watchdog:leader"
_INSTANCE_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class LeaderElection:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        ttl_sec: int = 30,
        on_elected: asyncio.coroutines = None,
        on_demoted: asyncio.coroutines = None,
    ) -> None:
        self.redis_url = redis_url
        self.ttl_sec = ttl_sec
        self.on_elected = on_elected
        self.on_demoted = on_demoted
        self._is_leader = False
        self._stop = asyncio.Event()
        self._redis = None

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            except ImportError:
                raise ImportError("HA leader election requires 'redis': pip install redis")
        return self._redis

    async def run(self) -> None:
        """Continuously compete for leadership. Call as asyncio task."""
        logger.info("Leader election started (instance=%s)", _INSTANCE_ID)
        while not self._stop.is_set():
            try:
                await self._compete()
            except Exception as exc:
                logger.warning("Leader election error: %s", exc)
            await asyncio.sleep(self.ttl_sec // 3)

    async def _compete(self) -> None:
        r = self._get_redis()
        ttl_ms = self.ttl_sec * 1000
        acquired = await r.set(_LEADER_KEY, _INSTANCE_ID, nx=True, px=ttl_ms)

        if acquired:
            if not self._is_leader:
                logger.info("Elected as leader: %s", _INSTANCE_ID)
                self._is_leader = True
                if self.on_elected:
                    await self.on_elected()
            else:
                # Renew the lease
                await r.pexpire(_LEADER_KEY, ttl_ms)
        else:
            current = await r.get(_LEADER_KEY)
            if current == _INSTANCE_ID:
                # We still hold it (race condition edge case)
                await r.pexpire(_LEADER_KEY, ttl_ms)
            elif self._is_leader:
                logger.warning("Lost leadership: %s", _INSTANCE_ID)
                self._is_leader = False
                if self.on_demoted:
                    await self.on_demoted()

    async def release(self) -> None:
        """Release leadership on graceful shutdown."""
        self._stop.set()
        if self._is_leader and self._redis:
            r = self._get_redis()
            current = await r.get(_LEADER_KEY)
            if current == _INSTANCE_ID:
                await r.delete(_LEADER_KEY)
                logger.info("Leadership released: %s", _INSTANCE_ID)
        self._is_leader = False
