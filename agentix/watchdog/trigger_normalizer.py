"""
Trigger Normalizer — converts raw channel payloads into a unified TriggerEnvelope.

TriggerEnvelope schema (matches architecture spec):
{
  "id":            str,   # trig_<ulid-like>
  "timestamp":     str,   # ISO-8601
  "channel":       str,   # http_webhook | slack | ...
  "channel_meta":  dict,
  "caller": {
      "identity_id": str,
      "roles":        list[str],
      "tenant_id":    str,
  },
  "payload": {
      "text":        str,
      "attachments": list,
      "context":     dict,
  },
  "agent_id":          str,
  "priority":          str,   # low | normal | high
  "idempotency_key":   str,
}
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any


def _new_id() -> str:
    return f"trig_{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def from_http(
    body: dict,
    headers: dict,
    agent_id: str,
    identity: dict | None = None,
) -> dict:
    """Normalise an HTTP webhook payload."""
    caller = identity or {
        "identity_id": headers.get("x-identity-id", "anonymous"),
        "roles": headers.get("x-roles", "end-user").split(","),
        "tenant_id": headers.get("x-tenant-id", "default"),
    }
    return {
        "id": _new_id(),
        "timestamp": _now_iso(),
        "channel": "http_webhook",
        "channel_meta": {
            "method": headers.get(":method", "POST"),
            "path": headers.get(":path", "/trigger"),
            "remote": headers.get("x-forwarded-for", ""),
        },
        "caller": caller,
        "payload": {
            "text": body.get("text", ""),
            "attachments": body.get("attachments", []),
            "context": body.get("context", {}),
        },
        "agent_id": agent_id or body.get("agent_id", ""),
        "priority": body.get("priority", "normal"),
        "idempotency_key": body.get("idempotency_key", _new_id()),
    }


def from_slack(event: dict, agent_id: str) -> dict:
    """Normalise a Slack Events API payload."""
    user = event.get("user", "unknown")
    return {
        "id": _new_id(),
        "timestamp": _now_iso(),
        "channel": "slack",
        "channel_meta": {
            "workspace": event.get("team", ""),
            "channel_id": event.get("channel", ""),
            "thread_ts": event.get("thread_ts", event.get("ts", "")),
            "event_type": event.get("type", ""),
        },
        "caller": {
            "identity_id": f"slack_user_{user}",
            "roles": ["end-user"],
            "tenant_id": event.get("team", "default"),
        },
        "payload": {
            "text": event.get("text", ""),
            "attachments": event.get("attachments", []),
            "context": {"files": event.get("files", [])},
        },
        "agent_id": agent_id,
        "priority": "normal",
        "idempotency_key": event.get("client_msg_id", event.get("ts", _new_id())),
    }


def from_scheduler(job: dict) -> dict:
    """Normalise a scheduled job trigger."""
    return {
        "id": _new_id(),
        "timestamp": _now_iso(),
        "channel": "scheduler",
        "channel_meta": {
            "schedule_name": job.get("name", ""),
            "expression": job.get("expression", ""),
        },
        "caller": {
            "identity_id": "scheduler-service",
            "roles": [job.get("run_as_role", "scheduler-service")],
            "tenant_id": job.get("tenant_id", "default"),
        },
        "payload": {
            "text": f"Scheduled job: {job.get('name', '')}",
            "attachments": [],
            "context": job.get("payload", {}),
        },
        "agent_id": job["agent"],
        "priority": job.get("priority", "normal"),
        "idempotency_key": f"sched_{job.get('name', '')}_{int(time.time())}",
    }
