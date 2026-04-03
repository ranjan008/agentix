"""
Distributed Rate Limiter — Redis sliding-window algorithm.

Replaces the in-memory RateLimiter from Phase 1 for multi-replica deployments.
Uses a Redis sorted set per identity: members are request timestamps,
trimmed to the current window on each check.

Falls back to in-memory if Redis is unavailable (degraded mode).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


class DistributedRateLimiter:
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        max_requests: int = 60,
        window_sec: int = 60,
        key_prefix: str = "agentix:ratelimit:",
    ) -> None:
        self.redis_url = redis_url
        self.max_requests = max_requests
        self.window_sec = window_sec
        self.key_prefix = key_prefix
        self._redis = None
        # In-memory fallback
        self._fallback: dict[str, list[float]] = defaultdict(list)
        self._redis_ok = True

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis
                self._redis = redis.from_url(self.redis_url, decode_responses=True, socket_timeout=0.5)
            except ImportError:
                return None
        return self._redis

    def check(self, identity_id: str) -> None:
        """Raises RateLimitError if identity exceeded quota."""
        try:
            r = self._get_redis()
            if r and self._redis_ok:
                self._check_redis(r, identity_id)
                return
        except Exception as e:
            logger.warning("Redis rate limiter unavailable (%s) — using in-memory fallback", e)
            self._redis_ok = False

        self._check_memory(identity_id)

    def _check_redis(self, r, identity_id: str) -> None:
        key = f"{self.key_prefix}{identity_id}"
        now = time.time()
        window_start = now - self.window_sec

        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window_sec + 1)
        _, count, *_ = pipe.execute()

        if count >= self.max_requests:
            from agentix.watchdog.auth import RateLimitError
            raise RateLimitError(
                f"Rate limit exceeded for {identity_id}: "
                f"{self.max_requests} requests per {self.window_sec}s"
            )

    def _check_memory(self, identity_id: str) -> None:
        now = time.time()
        window_start = now - self.window_sec
        self._fallback[identity_id] = [t for t in self._fallback[identity_id] if t > window_start]
        if len(self._fallback[identity_id]) >= self.max_requests:
            from agentix.watchdog.auth import RateLimitError
            raise RateLimitError(
                f"Rate limit exceeded for {identity_id}: "
                f"{self.max_requests} requests per {self.window_sec}s"
            )
        self._fallback[identity_id].append(now)
