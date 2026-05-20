"""
Test factories — build canonical test objects with sensible defaults.

Usage:
    from tests.factories import make_envelope, make_agent_spec, make_dag_spec
"""
from __future__ import annotations

import uuid
from typing import Any


def make_envelope(
    agent_id: str = "test-agent",
    text: str = "hello",
    channel: str = "http_webhook",
    trigger_id: str | None = None,
    identity: dict | None = None,
    context: dict | None = None,
) -> dict:
    """Return a minimal TriggerEnvelope dict."""
    tid = trigger_id or f"trig_{uuid.uuid4().hex[:16]}"
    return {
        "id": tid,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "channel": channel,
        "channel_meta": {},
        "caller": {
            "identity_id": (identity or {}).get("user_id", "user_test"),
            "roles": ["end-user"],
            "tenant_id": "default",
        },
        "payload": {
            "text": text,
            "attachments": [],
            "context": context or {},
        },
        "agent_id": agent_id,
        "priority": "normal",
        "idempotency_key": tid,
        "_identity": identity or {"user_id": "user_test"},
    }


def make_agent_spec(
    name: str = "test-agent",
    system_prompt: str = "You are a test agent.",
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    hitl: dict | None = None,
    state_schema: list[dict] | None = None,
    context_cfg: dict | None = None,
) -> dict:
    """Return a minimal agent spec dict (mirrors YAML spec.* structure)."""
    spec: dict[str, Any] = {
        "llm": {
            "provider": "anthropic",
            "model_id": "claude-haiku-4-5-20251001",
            "temperature": 0.0,
            "max_tokens": 256,
        },
        "system_prompt": system_prompt,
        "skills": skills or [],
        "execution": {"timeout_sec": 30, "max_tool_calls": 5},
        "memory": {"short_term": "sqlite", "scope": "session", "max_history_turns": 4, "ttl_sec": 3600},
    }
    if tools:
        spec["tools"] = tools
    if hitl:
        spec["hitl"] = hitl
    if state_schema:
        spec["state_schema"] = state_schema
    if context_cfg:
        spec["context"] = context_cfg
    return {
        "apiVersion": "agentix/v1",
        "kind": "Agent",
        "metadata": {"name": name, "version": "1.0.0"},
        "spec": spec,
    }


def make_dag_spec(
    name: str = "test-dag",
    steps: list[dict] | None = None,
    trigger_type: str = "cron",
    expression: str = "*/5 * * * *",
) -> dict:
    """Return a DAG schedule spec."""
    default_steps = [
        {"id": "extract", "agent": "extractor-agent", "depends_on": []},
        {"id": "transform", "agent": "transformer-agent", "depends_on": ["extract"]},
        {"id": "load", "agent": "loader-agent", "depends_on": ["transform"]},
    ]
    return {
        "name": name,
        "steps": steps or default_steps,
        "trigger": {"type": trigger_type, "expression": expression},
    }


def make_span(
    name: str = "test.span",
    trace_id: str | None = None,
    parent_id: str | None = None,
    duration_ms: int = 100,
    status: str = "ok",
    attributes: dict | None = None,
) -> dict:
    """Return a raw span dict for TraceStore tests."""
    import time
    return {
        "span_id": f"span_{uuid.uuid4().hex[:12]}",
        "trace_id": trace_id or f"trace_{uuid.uuid4().hex[:12]}",
        "parent_span_id": parent_id,
        "name": name,
        "start_time": time.time(),
        "duration_ms": duration_ms,
        "status": status,
        "attributes": attributes or {},
    }
