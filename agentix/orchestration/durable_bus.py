"""
Durable Event Bus — Redis Streams backend with Kafka adapter.

Replaces Phase 3 in-process EventBus for production deployments.

Features:
  - Redis Streams: durable, consumer-group based, per-agent lag tracking
  - At-least-once delivery with explicit ACK after successful spawn
  - Dead-letter stream for events that fail after max_retries
  - Event replay: re-emit any past event by stream ID range
  - Kafka adapter (same interface, swap via config)

Usage:
  bus = DurableEventBus.from_config(cfg, on_trigger=watchdog._handle_trigger)
  await bus.start()                         # starts consumer loop
  await bus.emit("order.created", payload, source_envelope)
  await bus.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

_STREAM_PREFIX = "agentix:events:"
_DLQ_STREAM = "agentix:events:dlq"
_GROUP_PREFIX = "agentix:consumers:"

OnTrigger = Callable[[dict], Awaitable[None]]


class RedisStreamsBus:
    """
    Redis Streams-backed durable event bus.
    Each event type maps to a dedicated stream key.
    Each subscribed agent gets its own consumer group for independent lag.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        on_trigger: OnTrigger | None = None,
        max_retries: int = 3,
        poll_interval_ms: int = 500,
    ) -> None:
        self.redis_url = redis_url
        self.on_trigger = on_trigger
        self.max_retries = max_retries
        self.poll_interval_ms = poll_interval_ms
        self._subscriptions: dict[str, list[tuple[str, Callable | None]]] = {}
        self._stop = asyncio.Event()
        self._redis = None

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis
                self._redis = redis.from_url(self.redis_url, decode_responses=True)
            except ImportError:
                raise ImportError("Durable event bus requires 'redis': pip install redis")
        return self._redis

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    def emit_sync(self, event_type: str, payload: dict, source_envelope: dict) -> str:
        """Publish an event to its stream. Returns stream entry ID."""
        r = self._get_redis()
        stream_key = f"{_STREAM_PREFIX}{event_type}"
        entry_id = r.xadd(stream_key, {
            "event_type": event_type,
            "payload": json.dumps(payload),
            "source_trigger_id": source_envelope.get("id", ""),
            "source_agent_id": source_envelope.get("agent_id", ""),
            "tenant_id": source_envelope.get("caller", {}).get("tenant_id", "default"),
            "ts": str(time.time()),
            "retry_count": "0",
        })
        logger.info("Event emitted: %s id=%s", event_type, entry_id)
        return entry_id

    async def emit(self, event_type: str, payload: dict, source_envelope: dict) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self.emit_sync(event_type, payload, source_envelope)
        )

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str, agent_id: str, filter_fn: Callable | None = None) -> None:
        self._subscriptions.setdefault(event_type, [])
        self._subscriptions[event_type].append((agent_id, filter_fn))
        # Ensure consumer group exists
        try:
            r = self._get_redis()
            stream_key = f"{_STREAM_PREFIX}{event_type}"
            group = f"{_GROUP_PREFIX}{agent_id}"
            try:
                r.xgroup_create(stream_key, group, id="0", mkstream=True)
            except Exception:
                pass  # Group already exists
        except Exception as e:
            logger.warning("Could not create consumer group: %s", e)
        logger.info("DurableBus: agent '%s' subscribed to '%s'", agent_id, event_type)

    def load_subscriptions(self, subscriptions: list[dict]) -> None:
        for sub in subscriptions:
            self.subscribe(sub["event"], sub["agent"])

    # ------------------------------------------------------------------
    # Consumer loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        asyncio.create_task(self._consumer_loop(), name="event-bus-consumer")
        logger.info("Durable event bus started (Redis Streams)")

    async def stop(self) -> None:
        self._stop.set()

    async def _consumer_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            try:
                await loop.run_in_executor(None, self._poll_all)
            except Exception as exc:
                logger.exception("Event bus consumer error: %s", exc)
            await asyncio.sleep(self.poll_interval_ms / 1000)

    def _poll_all(self) -> None:
        r = self._get_redis()
        for event_type, subs in self._subscriptions.items():
            stream_key = f"{_STREAM_PREFIX}{event_type}"
            for agent_id, filter_fn in subs:
                group = f"{_GROUP_PREFIX}{agent_id}"
                try:
                    messages = r.xreadgroup(
                        group, f"worker-{agent_id}",
                        {stream_key: ">"},
                        count=10, block=0,
                    )
                    if messages:
                        for _, entries in messages:
                            for entry_id, fields in entries:
                                self._process_entry(
                                    r, stream_key, group, entry_id, fields,
                                    agent_id, filter_fn,
                                )
                except Exception as e:
                    logger.debug("Poll error for %s/%s: %s", event_type, agent_id, e)

    def _process_entry(self, r, stream_key, group, entry_id, fields, agent_id, filter_fn):
        try:
            payload = json.loads(fields.get("payload", "{}"))
            if filter_fn and not filter_fn(payload):
                r.xack(stream_key, group, entry_id)
                return

            # Build a minimal source envelope for child trigger construction
            from agentix.watchdog.trigger_normalizer import from_http
            source_envelope = {
                "id": fields.get("source_trigger_id", ""),
                "agent_id": fields.get("source_agent_id", ""),
                "caller": {
                    "identity_id": "event-bus",
                    "roles": ["operator"],
                    "tenant_id": fields.get("tenant_id", "default"),
                },
                "channel": "event_bus",
            }

            child = from_http(
                body={"text": payload.get("text", f"Event: {fields.get('event_type', '')}"),
                      "agent_id": agent_id, "context": payload},
                headers={"x-identity-id": "event-bus",
                         "x-roles": "operator",
                         "x-tenant-id": fields.get("tenant_id", "default")},
                agent_id=agent_id,
            )
            child["payload"]["context"]["event_type"] = fields.get("event_type", "")
            child["payload"]["context"]["stream_entry_id"] = entry_id

            if self.on_trigger:
                asyncio.create_task(self.on_trigger(child))

            r.xack(stream_key, group, entry_id)

        except Exception as exc:
            retry_count = int(fields.get("retry_count", 0)) + 1
            logger.error("Event processing failed (attempt %d): %s", retry_count, exc)
            if retry_count >= self.max_retries:
                # Move to dead-letter queue
                r.xadd(_DLQ_STREAM, {**fields, "retry_count": str(retry_count),
                                      "error": str(exc), "failed_at": str(time.time())})
                r.xack(stream_key, group, entry_id)
                logger.error("Event moved to DLQ: %s", entry_id)
            else:
                # Re-emit with incremented retry count (will be re-read on next poll)
                r.xadd(stream_key, {**fields, "retry_count": str(retry_count)})
                r.xack(stream_key, group, entry_id)

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(self, event_type: str, start_id: str = "0", end_id: str = "+", count: int = 100) -> list[dict]:
        """Read historical events from a stream (for debugging/replay)."""
        r = self._get_redis()
        stream_key = f"{_STREAM_PREFIX}{event_type}"
        messages = r.xrange(stream_key, start_id, end_id, count=count)
        return [{"id": mid, **{k: v for k, v in fields.items()}} for mid, fields in messages]

    def dlq_entries(self, count: int = 50) -> list[dict]:
        """Inspect dead-letter queue entries."""
        r = self._get_redis()
        messages = r.xrange(_DLQ_STREAM, "-", "+", count=count)
        return [{"id": mid, **{k: v for k, v in fields.items()}} for mid, fields in messages]


# ---------------------------------------------------------------------------
# Kafka adapter (same interface)
# ---------------------------------------------------------------------------

class KafkaBus:
    """
    Kafka-backed event bus. Requires: pip install confluent-kafka
    Same subscribe/emit/start/stop interface as RedisStreamsBus.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        on_trigger: OnTrigger | None = None,
        group_id: str = "agentix-consumers",
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.on_trigger = on_trigger
        self.group_id = group_id
        self._subscriptions: dict[str, list[tuple[str, Callable | None]]] = {}
        self._stop = asyncio.Event()

    def subscribe(self, event_type: str, agent_id: str, filter_fn: Callable | None = None) -> None:
        self._subscriptions.setdefault(event_type, [])
        self._subscriptions[event_type].append((agent_id, filter_fn))

    def load_subscriptions(self, subscriptions: list[dict]) -> None:
        for sub in subscriptions:
            self.subscribe(sub["event"], sub["agent"])

    async def emit(self, event_type: str, payload: dict, source_envelope: dict) -> str:
        try:
            from confluent_kafka import Producer
            p = Producer({"bootstrap.servers": self.bootstrap_servers})
            msg = json.dumps({"event_type": event_type, "payload": payload,
                              "source_trigger_id": source_envelope.get("id", ""),
                              "ts": time.time()}).encode()
            p.produce(f"agentix.events.{event_type}", msg)
            p.flush(timeout=5)
            return f"kafka:{event_type}:{time.time()}"
        except ImportError:
            raise ImportError("Kafka bus requires 'confluent-kafka': pip install confluent-kafka")

    async def start(self) -> None:
        asyncio.create_task(self._consumer_loop(), name="kafka-bus-consumer")
        logger.info("Durable event bus started (Kafka: %s)", self.bootstrap_servers)

    async def stop(self) -> None:
        self._stop.set()

    async def _consumer_loop(self) -> None:
        try:
            from confluent_kafka import Consumer, KafkaError
        except ImportError:
            logger.error("Kafka consumer requires 'confluent-kafka': pip install confluent-kafka")
            return

        topics = [f"agentix.events.{et}" for et in self._subscriptions]
        if not topics:
            return

        consumer = Consumer({
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "auto.offset.reset": "earliest",
        })
        consumer.subscribe(topics)

        loop = asyncio.get_event_loop()
        while not self._stop.is_set():
            msg = await loop.run_in_executor(None, lambda: consumer.poll(1.0))
            if msg is None or msg.error():
                continue
            try:
                data = json.loads(msg.value())
                event_type = data.get("event_type", "")
                for agent_id, filter_fn in self._subscriptions.get(event_type, []):
                    payload = data.get("payload", {})
                    if filter_fn and not filter_fn(payload):
                        continue
                    from agentix.watchdog.trigger_normalizer import from_http
                    child = from_http(
                        body={"text": payload.get("text", f"Event: {event_type}"),
                              "agent_id": agent_id, "context": payload},
                        headers={"x-identity-id": "event-bus", "x-roles": "operator",
                                 "x-tenant-id": "default"},
                        agent_id=agent_id,
                    )
                    if self.on_trigger:
                        await self.on_trigger(child)
                consumer.commit(asynchronous=True)
            except Exception as exc:
                logger.exception("Kafka consumer error: %s", exc)

        consumer.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_event_bus(cfg: dict, on_trigger: OnTrigger) -> RedisStreamsBus | KafkaBus:
    bus_cfg = cfg.get("event_bus", {})
    backend = bus_cfg.get("backend", "memory")

    if backend == "redis":
        redis_url = cfg.get("redis_url", "redis://localhost:6379/0")
        bus = RedisStreamsBus(redis_url=redis_url, on_trigger=on_trigger,
                              max_retries=bus_cfg.get("max_retries", 3))
    elif backend == "kafka":
        bus = KafkaBus(bootstrap_servers=bus_cfg.get("bootstrap_servers", "localhost:9092"),
                       on_trigger=on_trigger, group_id=bus_cfg.get("group_id", "agentix"))
    else:
        # Fall back to Phase 3 in-process bus
        from agentix.orchestration.patterns import EventBus
        bus = EventBus(on_trigger=on_trigger)

    for sub in cfg.get("event_subscriptions", []):
        bus.subscribe(sub["event"], sub["agent"])
    return bus
