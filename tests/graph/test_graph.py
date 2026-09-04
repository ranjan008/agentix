"""
Tests for the StateGraph engine.

Covers:
  - StateSchema reducers (replace, append, merge, add, custom)
  - StateGraph construction (nodes, edges, conditional edges)
  - CompiledGraph.invoke() — linear, conditional, cycle-breaking
  - AgentNode state patches
  - ToolNode tool execution
  - RouterNode routing
  - LambdaNode wrapping
"""
from __future__ import annotations

import pytest

from agentix.graph.state import FieldSchema, StateSchema
from agentix.graph.nodes import AgentNode, LambdaNode, RouterNode, ToolNode
from agentix.graph.graph import END, StateGraph


# ===========================================================================
# StateSchema
# ===========================================================================

class TestStateSchema:
    def _schema(self):
        return StateSchema({
            "messages":    FieldSchema(default=[], reducer="append"),
            "tool_calls":  FieldSchema(default=[], reducer="append"),
            "count":       FieldSchema(default=0,  reducer="add"),
            "meta":        FieldSchema(default={}, reducer="merge"),
            "answer":      FieldSchema(reducer="replace"),
        })

    def test_initial_has_all_defaults(self):
        s = self._schema()
        state = s.initial()
        assert state["messages"] == []
        assert state["count"] == 0
        assert state["meta"] == {}
        assert state["answer"] is None

    def test_replace_reducer(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"answer": "hello"})
        state = s.update(state, {"answer": "world"})
        assert state["answer"] == "world"

    def test_append_reducer(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"messages": [{"role": "user", "content": "hi"}]})
        state = s.update(state, {"messages": [{"role": "assistant", "content": "hello"}]})
        assert len(state["messages"]) == 2

    def test_append_single_item(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"messages": {"role": "user", "content": "hi"}})
        assert len(state["messages"]) == 1

    def test_add_reducer(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"count": 5})
        state = s.update(state, {"count": 3})
        assert state["count"] == 8

    def test_merge_reducer(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"meta": {"a": 1}})
        state = s.update(state, {"meta": {"b": 2}})
        assert state["meta"] == {"a": 1, "b": 2}

    def test_unknown_key_passes_through(self):
        s = self._schema()
        state = s.initial()
        state = s.update(state, {"custom_key": "custom_value"})
        assert state["custom_key"] == "custom_value"

    def test_custom_callable_reducer(self):
        schema = StateSchema({
            "tags": FieldSchema(default=set(), reducer=lambda old, new: old | new),
        })
        state = schema.initial()
        state = schema.update(state, {"tags": {"a", "b"}})
        state = schema.update(state, {"tags": {"b", "c"}})
        assert state["tags"] == {"a", "b", "c"}

    def test_invalid_reducer_name_raises(self):
        with pytest.raises(ValueError, match="Unknown reducer"):
            FieldSchema(reducer="invalid_reducer_name")

    def test_initial_does_not_share_mutable_defaults(self):
        s = self._schema()
        s1 = s.initial()
        s2 = s.initial()
        s1["messages"].append("x")
        assert s2["messages"] == []


# ===========================================================================
# LambdaNode
# ===========================================================================

@pytest.mark.asyncio
async def test_lambda_node_sync():
    node = LambdaNode("greet", lambda s: {"answer": f"hello {s.get('name', 'world')}"})
    patch = await node.run({"name": "Alice"})
    assert patch["answer"] == "hello Alice"


@pytest.mark.asyncio
async def test_lambda_node_async():
    async def fn(state):
        return {"answer": "async result"}
    node = LambdaNode("async_node", fn)
    patch = await node.run({})
    assert patch["answer"] == "async result"


@pytest.mark.asyncio
async def test_lambda_node_returns_none_gives_empty_patch():
    node = LambdaNode("noop", lambda s: None)
    patch = await node.run({})
    assert patch == {}


# ===========================================================================
# RouterNode
# ===========================================================================

@pytest.mark.asyncio
async def test_router_node_run_returns_empty_patch():
    router = RouterNode("router", condition=lambda s: "tools" if s.get("tool_calls") else END)
    patch = await router.run({"tool_calls": []})
    assert patch == {}


def test_router_node_route_with_tool_calls():
    router = RouterNode("router", condition=lambda s: "tools" if s.get("tool_calls") else END)
    assert router.route({"tool_calls": [{"id": "1"}]}) == "tools"
    assert router.route({"tool_calls": []}) == END


# ===========================================================================
# AgentNode (with mock LLM)
# ===========================================================================

class _MockResponse:
    def __init__(self, content, stop_reason, tool_calls=None):
        self.content = content
        self.stop_reason = stop_reason
        self.tool_calls = tool_calls or []
        self.raw = {"blocks": [{"type": "text", "text": content}]}
        self.input_tokens = 10
        self.output_tokens = 20


class MockLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_agent_node_end_turn():
    llm = MockLLM([_MockResponse("The answer is 42", "end_turn")])
    node = AgentNode("agent", llm, output_key="final_answer")
    patch = await node.run({"messages": [{"role": "user", "content": "What is 6*7?"}]})
    assert patch["final_answer"] == "The answer is 42"
    assert patch["stop_reason"] == "end_turn"
    assert any(m.get("role") == "assistant" for m in patch["messages"])


@pytest.mark.asyncio
async def test_agent_node_tool_use():
    class _TC:
        id = "tc_1"
        name = "web_search"
        input = {"query": "test"}

    llm = MockLLM([_MockResponse("", "tool_use", tool_calls=[_TC()])])
    node = AgentNode("agent", llm)
    patch = await node.run({"messages": []})
    assert patch["stop_reason"] == "tool_use"
    assert len(patch["tool_calls"]) == 1
    assert patch["tool_calls"][0]["name"] == "web_search"


@pytest.mark.asyncio
async def test_agent_node_tool_use_non_anthropic_provider_builds_tool_use_block():
    """Regression test: AgentNode used to only build a proper `tool_use`
    content block when `response.raw` was a dict with a "blocks" key —
    which only AnthropicProvider ever populates. Every other provider
    (GeminiProvider, the OpenAI-compatible local_provider.py backing
    ollama/lmstudio/vllm/local, openai_provider.py) returns `raw` as its
    own SDK response object instead, so the old code silently fell back
    to storing plain text with no tool_use block at all — breaking the
    very next turn's tool_result validation ("unexpected tool_use_id
    found in tool_result blocks: ... no corresponding tool_use block in
    the previous message"). Reproduces that shape here: `raw` is a plain
    object, not a dict, exactly like GeminiProvider/local_provider.py
    actually return."""
    class _NonDictRaw:
        pass

    class _TC:
        id = "tc_1"
        name = "web_search"
        input = {"query": "test"}

    resp = _MockResponse("", "tool_use", tool_calls=[_TC()])
    resp.raw = _NonDictRaw()  # simulate a non-Anthropic provider's raw response

    llm = MockLLM([resp])
    node = AgentNode("agent", llm)
    patch = await node.run({"messages": []})

    assert patch["stop_reason"] == "tool_use"
    assistant_msg = patch["messages"][0]
    assert assistant_msg["role"] == "assistant"
    content = assistant_msg["content"]
    assert isinstance(content, list), "content must be content blocks, not a plain string, or tool_result can never validly reference it"
    tool_use_blocks = [b for b in content if b.get("type") == "tool_use"]
    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0]["id"] == "tc_1"
    assert tool_use_blocks[0]["name"] == "web_search"
    assert tool_use_blocks[0]["input"] == {"query": "test"}


@pytest.mark.asyncio
async def test_agent_node_accumulates_tokens():
    llm = MockLLM([_MockResponse("done", "end_turn")])
    node = AgentNode("agent", llm)
    patch = await node.run({"messages": []})
    assert patch.get("token_count", 0) == 30  # 10 + 20 from mock


# ===========================================================================
# AgentNode — agent -> agent handoff (no ToolNode in between)
# ===========================================================================
#
# Found live: a two-node graph (research -> draft, a direct edge, the most
# ordinary shape a graph can have) failed on Anthropic with a real 400 -
# "This model does not support assistant message prefill. The conversation
# must end with a user message." research's own end_turn branch (above)
# writes {"role": "assistant", ...} into state["messages"] (reducer:
# append); draft then read that same list, still ending on assistant, and
# sent it straight to the provider. agent -> tool -> agent never hit this -
# ToolNode always appends a role: "user" tool_result message first - only
# a direct agent -> agent edge does.

@pytest.mark.asyncio
async def test_agent_node_injects_continuation_after_prior_assistant_turn():
    llm = MockLLM([_MockResponse("draft text", "end_turn")])
    node = AgentNode("draft", llm, output_key="final_answer")
    prior_state = {
        "messages": [
            {"role": "user", "content": "Research this topic"},
            {"role": "assistant", "content": "Here is what I found"},  # research's own output
        ]
    }
    await node.run(prior_state)

    sent = llm.calls[0]["messages"]
    assert sent[-1] == {"role": "user", "content": "Continue."}
    assert sent[-2] == {"role": "assistant", "content": "Here is what I found"}


@pytest.mark.asyncio
async def test_agent_node_no_continuation_when_already_ends_on_user():
    """The normal case (first node in a graph, or after a ToolNode) must
    not get an extra turn injected."""
    llm = MockLLM([_MockResponse("answer", "end_turn")])
    node = AgentNode("agent", llm)
    await node.run({"messages": [{"role": "user", "content": "hi"}]})

    sent = llm.calls[0]["messages"]
    assert sent == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_agent_node_continuation_turn_not_persisted_to_state():
    """The injected turn is local to this one call, not written back —
    each node independently derives whether it needs one from whatever it
    actually reads, so state must not accumulate synthetic "Continue."
    messages every hop."""
    llm = MockLLM([_MockResponse("draft text", "end_turn")])
    node = AgentNode("draft", llm, output_key="final_answer")
    patch = await node.run({
        "messages": [
            {"role": "user", "content": "Research this topic"},
            {"role": "assistant", "content": "Here is what I found"},
        ]
    })
    assert patch["messages"] == [{"role": "assistant", "content": "draft text"}]
    assert not any(m.get("content") == "Continue." for m in patch["messages"])


# ===========================================================================
# ToolNode (with mock executor)
# ===========================================================================

class MockExecutor:
    def __init__(self, results):
        self._results = dict(results)
    async def execute(self, name, input):
        if name not in self._results:
            raise KeyError(f"No mock for {name}")
        return self._results[name]


@pytest.mark.asyncio
async def test_tool_node_executes_tool_calls():
    executor = MockExecutor({"web_search": "search results"})
    node = ToolNode("tools", executor)
    patch = await node.run({
        "tool_calls": [{"id": "tc_1", "name": "web_search", "input": {"query": "test"}}]
    })
    messages = patch["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    results = messages[0]["content"]
    assert any(r.get("tool_use_id") == "tc_1" for r in results)
    assert patch["tool_calls"] == []  # cleared


@pytest.mark.asyncio
async def test_tool_node_handles_error():
    executor = MockExecutor({})
    node = ToolNode("tools", executor)
    patch = await node.run({
        "tool_calls": [{"id": "tc_err", "name": "missing_tool", "input": {}}]
    })
    results = patch["messages"][0]["content"]
    assert results[0].get("is_error") is True


@pytest.mark.asyncio
async def test_tool_node_empty_tool_calls_noop():
    executor = MockExecutor({})
    node = ToolNode("tools", executor)
    patch = await node.run({"tool_calls": []})
    assert patch == {}


# ===========================================================================
# StateGraph + CompiledGraph (integration)
# ===========================================================================

@pytest.mark.asyncio
async def test_linear_graph():
    """node_a → node_b → __end__"""
    schema = StateSchema({
        "steps": FieldSchema(default=[], reducer="append"),
    })

    node_a = LambdaNode("a", lambda s: {"steps": ["a"]})
    node_b = LambdaNode("b", lambda s: {"steps": ["b"]})

    g = StateGraph(schema)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.set_entry("a")
    g.add_edge("a", "b")
    g.add_edge("b", END)

    runner = g.compile()
    state = await runner.invoke()
    assert state["steps"] == ["a", "b"]
    assert state["__steps__"] == 2


@pytest.mark.asyncio
async def test_conditional_routing():
    """agent routes to tools when tool_calls present, else to __end__"""
    schema = StateSchema({
        "messages": FieldSchema(default=[], reducer="append"),
        "tool_calls": FieldSchema(default=[], reducer="replace"),
        "routed_to": FieldSchema(reducer="append"),
    })

    def agent_fn(state):
        # First call: emit tool_calls; second call: end
        if not state.get("_done"):
            return {"tool_calls": [{"id": "1", "name": "x", "input": {}}], "_done": True}
        return {"tool_calls": [], "routed_to": ["agent_end"]}

    def tools_fn(state):
        return {"routed_to": ["tools"], "tool_calls": []}

    g = StateGraph(schema)
    g.add_node("agent", LambdaNode("agent", agent_fn))
    g.add_node("tools", LambdaNode("tools", tools_fn))
    g.set_entry("agent")
    g.add_conditional_edge(
        "agent",
        condition=lambda s: "tools" if s.get("tool_calls") else END,
    )
    g.add_edge("tools", "agent")

    runner = g.compile()
    state = await runner.invoke()
    assert "tools" in state.get("routed_to", [])


@pytest.mark.asyncio
async def test_graph_no_schema():
    """Graph works without a StateSchema (plain dict passthrough)."""
    g = StateGraph()
    g.add_node("n", LambdaNode("n", lambda s: {"x": s.get("x", 0) + 1}))
    g.set_entry("n")
    g.add_edge("n", END)

    runner = g.compile()
    state = await runner.invoke({"x": 10})
    assert state["x"] == 11


@pytest.mark.asyncio
async def test_graph_max_steps_guard():
    """Cycle without end: graph stops at max_steps."""
    g = StateGraph()
    g.add_node("loop", LambdaNode("loop", lambda s: {"count": s.get("count", 0) + 1}))
    g.set_entry("loop")
    g.add_edge("loop", "loop")  # infinite cycle

    runner = g.compile()
    state = await runner.invoke(max_steps=5)
    assert state["__max_steps_reached__"] is True
    assert state["__steps__"] == 5


@pytest.mark.asyncio
async def test_graph_unknown_node_raises():
    g = StateGraph()
    g.add_node("a", LambdaNode("a", lambda s: {}))
    g.set_entry("a")
    g.add_edge("a", "nonexistent")

    runner = g.compile()
    with pytest.raises(ValueError, match="not registered"):
        await runner.invoke()


def test_graph_reserved_name_raises():
    g = StateGraph()
    with pytest.raises(ValueError, match="reserved"):
        g.add_node(END, LambdaNode("x", lambda s: {}))


def test_compile_without_entry_raises():
    g = StateGraph()
    g.add_node("a", LambdaNode("a", lambda s: {}))
    with pytest.raises(ValueError, match="entry"):
        g.compile()


# ===========================================================================
# AgentNode — messages_key parameter
# ===========================================================================

@pytest.mark.asyncio
async def test_agent_node_custom_messages_key():
    """AgentNode reads from a custom state field when messages_key is set."""
    llm = MockLLM([_MockResponse("synthesis result", "end_turn")])
    node = AgentNode("synth", llm, output_key="final_answer", messages_key="synth_messages")
    state = {
        "messages": [{"role": "user", "content": "ignored"}],
        "synth_messages": [{"role": "user", "content": "research notes here"}],
    }
    patch = await node.run(state)
    # LLM should have been called with synth_messages, not messages
    assert patch["final_answer"] == "synthesis result"
    # Verify: the LLM call used the synth_messages content
    assert llm.calls[0]["messages"][0]["content"] == "research notes here"


@pytest.mark.asyncio
async def test_agent_node_missing_messages_key_gives_empty():
    """AgentNode returns empty list when custom messages_key is absent from state."""
    llm = MockLLM([_MockResponse("ok", "end_turn")])
    node = AgentNode("agent", llm, messages_key="custom_key")
    patch = await node.run({})   # state has no "custom_key"
    assert patch["final_answer"] == "ok"
    assert llm.calls[0]["messages"] == []
