"""Tests for context_builder.py's durable-memory functions.

Durable memory is distinct from conversation_history (the existing rolling
transcript window, unaffected by these tests): a small set of standing
facts about a calling identity, extracted after a turn completes and
recalled on every future call regardless of the rolling window's turn
count or elapsed time. Plan-gated upstream (Yantra's builder-svc) — this
module only executes what's already true in a saved spec.
"""
from __future__ import annotations

import pytest

from agentix.agent_runtime.context_builder import (
    MAX_DURABLE_FACTS,
    build_system_prompt,
    get_durable_facts,
    maybe_extract_durable_memory,
)


class _FakeStore:
    """Mimics StateStore's set_state/get_state exactly enough for these
    tests — a plain dict keyed the same way the real SQLite-backed store
    keys agent_state rows."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, str], object] = {}

    def set_state(self, agent_id, scope, key, value, ttl_sec=None) -> None:
        self._data[(agent_id, scope, key)] = value

    def get_state(self, agent_id, scope, key):
        return self._data.get((agent_id, scope, key))


class _FakeLLMResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return _FakeLLMResponse(self._content)


# ---------------------------------------------------------------------------
# build_system_prompt — recall side
# ---------------------------------------------------------------------------

def test_build_system_prompt_without_durable_facts_unchanged():
    spec = {"metadata": {"name": "agent"}, "spec": {"system_prompt": "You help with X."}}
    prompt = build_system_prompt(spec, [])
    assert prompt == "You help with X."


def test_build_system_prompt_appends_durable_facts_section():
    spec = {"metadata": {"name": "agent"}, "spec": {"system_prompt": "You help with X."}}
    facts = [{"text": "prefers formal tone"}, {"text": "timezone is PST"}]
    prompt = build_system_prompt(spec, [], durable_facts=facts)
    assert "What you remember about this user from past conversations:" in prompt
    assert "- prefers formal tone" in prompt
    assert "- timezone is PST" in prompt


def test_build_system_prompt_empty_facts_list_adds_nothing():
    spec = {"metadata": {"name": "agent"}, "spec": {"system_prompt": "You help with X."}}
    prompt = build_system_prompt(spec, [], durable_facts=[])
    assert "remember" not in prompt.lower()


# ---------------------------------------------------------------------------
# get_durable_facts
# ---------------------------------------------------------------------------

def test_get_durable_facts_empty_for_unknown_identity():
    store = _FakeStore()
    assert get_durable_facts(store, "agent", "user:nobody") == []


def test_get_durable_facts_returns_stored_value():
    store = _FakeStore()
    store.set_state("agent", "user:alice", "durable_facts", [{"text": "x"}])
    assert get_durable_facts(store, "agent", "user:alice") == [{"text": "x"}]


# ---------------------------------------------------------------------------
# maybe_extract_durable_memory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extraction_is_a_noop_when_memory_durable_not_set():
    """The actual gate: an agent whose spec doesn't set memory.durable
    (the overwhelming majority — this is Team-plan-only, enforced upstream
    in builder-svc before a spec can ever have this true) must make ZERO
    LLM calls and write nothing."""
    spec = {"spec": {}}  # no memory.durable at all
    store = _FakeStore()
    llm = _FakeLLM('["should never be extracted"]')

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "hi", "hello", llm, store)

    assert llm.calls == []
    assert get_durable_facts(store, "agent", "user:alice") == []


@pytest.mark.asyncio
async def test_extraction_stores_new_facts_when_durable_is_set():
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    llm = _FakeLLM('["timezone is PST"]')

    await maybe_extract_durable_memory(
        spec, "agent", "user:alice", "I'm in PST", "Got it, noting your timezone.", llm, store,
    )

    facts = get_durable_facts(store, "agent", "user:alice")
    assert len(facts) == 1
    assert facts[0]["text"] == "timezone is PST"
    assert "learned_at" in facts[0]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_extraction_merges_with_existing_facts():
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    store.set_state("agent", "user:alice", "durable_facts", [{"text": "prefers formal tone", "learned_at": "t0"}])
    llm = _FakeLLM('["timezone is PST"]')

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", llm, store)

    facts = get_durable_facts(store, "agent", "user:alice")
    assert {f["text"] for f in facts} == {"prefers formal tone", "timezone is PST"}


@pytest.mark.asyncio
async def test_extraction_empty_array_response_leaves_facts_unchanged():
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    llm = _FakeLLM("[]")

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", llm, store)

    assert get_durable_facts(store, "agent", "user:alice") == []


@pytest.mark.asyncio
async def test_extraction_caps_at_max_durable_facts():
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    store.set_state(
        "agent", "user:alice", "durable_facts",
        [{"text": f"fact {i}", "learned_at": "t"} for i in range(MAX_DURABLE_FACTS)],
    )
    llm = _FakeLLM('["a brand new fact"]')

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", llm, store)

    facts = get_durable_facts(store, "agent", "user:alice")
    assert len(facts) == MAX_DURABLE_FACTS  # still capped, not MAX+1
    assert facts[-1]["text"] == "a brand new fact"  # newest kept
    assert facts[0]["text"] == "fact 1"              # oldest (fact 0) evicted


@pytest.mark.asyncio
async def test_extraction_malformed_json_is_swallowed_not_raised():
    """A model that doesn't follow the JSON-only instruction must not
    break the actual response the caller is waiting on — this runs after
    the real answer was already computed."""
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    llm = _FakeLLM("I don't think there's anything worth remembering here.")

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", llm, store)  # must not raise

    assert get_durable_facts(store, "agent", "user:alice") == []


@pytest.mark.asyncio
async def test_extraction_handles_code_fenced_json():
    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()
    llm = _FakeLLM('```json\n["timezone is PST"]\n```')

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", llm, store)

    assert get_durable_facts(store, "agent", "user:alice")[0]["text"] == "timezone is PST"


@pytest.mark.asyncio
async def test_extraction_llm_exception_is_swallowed_not_raised():
    class _RaisingLLM:
        async def complete(self, *a, **k):
            raise RuntimeError("provider down")

    spec = {"spec": {"memory": {"durable": True}}}
    store = _FakeStore()

    await maybe_extract_durable_memory(spec, "agent", "user:alice", "u", "a", _RaisingLLM(), store)  # must not raise

    assert get_durable_facts(store, "agent", "user:alice") == []
