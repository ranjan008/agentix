"""
Reusable test fixtures for agent specs and trigger envelopes.

Usage with pytest:

  from agentix.testing.fixtures import minimal_agent_spec, make_trigger

  spec = minimal_agent_spec(name="my-agent", skills=["web_search"])
  harness = AgentTestHarness.from_dict(spec)
"""
from __future__ import annotations

import time
import uuid


def minimal_agent_spec(
    name: str = "test-agent",
    system_prompt: str = "You are a helpful assistant.",
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    model: str = "mock",
    tags: list[str] | None = None,
) -> dict:
    return {
        "apiVersion": "agentix/v1",
        "kind": "Agent",
        "metadata": {
            "name": name,
            "version": "0.0.1-test",
            "description": f"Test agent: {name}",
        },
        "spec": {
            "model": model,
            "system_prompt": system_prompt,
            "skills": skills or [],
            "tools": tools or [],
            "tags": tags or [],
            "memory": {"ttl_sec": 300},
        },
    }


def make_trigger(
    agent_id: str = "test-agent",
    text: str = "Hello",
    identity_id: str = "test-user",
    tenant_id: str = "default",
    channel: str = "test",
    extra_payload: dict | None = None,
) -> dict:
    return {
        "id": f"trig_{uuid.uuid4().hex[:16]}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "channel": channel,
        "channel_meta": {},
        "caller": {
            "identity_id": identity_id,
            "roles": ["operator"],
            "tenant_id": tenant_id,
        },
        "payload": {
            "text": text,
            "attachments": [],
            "context": extra_payload or {},
        },
        "agent_id": agent_id,
        "priority": "normal",
        "idempotency_key": uuid.uuid4().hex,
    }
