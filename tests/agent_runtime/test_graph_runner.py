"""
Tests for graph_runner — YAML graph spec → StateGraph compilation and execution.

Covers:
  - build_graph_from_spec: node/edge wiring, entry point, state schema
  - run_graph: initial state seeding, envelope mapping, final state
  - _TracingNode: span recording per node
  - Safe eval namespace: builtins available in lambda/router conditions
  - Error paths: missing entry, unknown node type, missing condition
"""
from __future__ import annotations

import pytest

from agentix.agent_runtime.graph_runner import build_graph_from_spec, run_graph, _eval_fn


# ---------------------------------------------------------------------------
# Helpers — minimal mock LLM and executor
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, content="done", stop_reason="end_turn", tool_calls=None):
        self.content = content
        self.stop_reason = stop_reason
        self.tool_calls = tool_calls or []
        self.raw = {"blocks": [{"type": "text", "text": content}]}
        self.input_tokens = 5
        self.output_tokens = 10


class MockLLM:
    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._responses:
            return self._responses.pop(0)
        return _MockResponse()


class MockExecutor:
    def __init__(self, results=None):
        self._results = dict(results or {})

    async def execute(self, name, input):
        return self._results.get(name, f"result of {name}")


# ---------------------------------------------------------------------------
# Minimal graph specs for testing
# ---------------------------------------------------------------------------

def _linear_spec(entry="greeter"):
    return {
        "entry": entry,
        "nodes": [
            {"id": "greeter", "type": "lambda", "fn": "lambda state: {'final_answer': 'hello'}"},
        ],
        "edges": [
            {"from": "greeter", "to": "__end__"},
        ],
    }


def _agent_spec(output_key="final_answer"):
    return {
        "entry": "agent",
        "state_schema": {
            "messages": {"reducer": "append", "default": []},
            "tool_calls": {"reducer": "replace", "default": []},
            "final_answer": {"reducer": "replace", "default": ""},
            "token_count": {"reducer": "add", "default": 0},
        },
        "nodes": [
            {
                "id": "agent",
                "type": "agent",
                "system_prompt": "You are a test agent.",
                "output_key": output_key,
            },
        ],
        "edges": [
            {"from": "agent", "to": "__end__"},
        ],
    }


# ---------------------------------------------------------------------------
# _eval_fn
# ---------------------------------------------------------------------------

def test_eval_fn_simple_lambda():
    fn = _eval_fn("lambda x: x + 1")
    assert fn(5) == 6


def test_eval_fn_len_available():
    fn = _eval_fn("lambda state: len(state.get('items', []))")
    assert fn({"items": [1, 2, 3]}) == 3


def test_eval_fn_str_int_available():
    fn = _eval_fn("lambda state: str(state.get('count', 0))")
    assert fn({"count": 42}) == "42"


def test_eval_fn_invalid_raises():
    with pytest.raises(ValueError, match="Failed to evaluate"):
        _eval_fn("not valid python !!!")


def test_eval_fn_dangerous_builtins_blocked():
    # __import__ must not be accessible
    with pytest.raises((ValueError, NameError, TypeError)):
        fn = _eval_fn("lambda s: __import__('os')")
        fn({})


# ---------------------------------------------------------------------------
# build_graph_from_spec — structure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_simple_lambda_graph():
    spec = _linear_spec()
    compiled = build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])
    state = await compiled.invoke()
    assert state["final_answer"] == "hello"


@pytest.mark.asyncio
async def test_build_graph_missing_entry_raises():
    spec = {
        "nodes": [{"id": "a", "type": "lambda", "fn": "lambda s: {}"}],
        "edges": [{"from": "a", "to": "__end__"}],
    }
    with pytest.raises(ValueError, match="entry"):
        build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])


def test_build_graph_unknown_node_type_raises():
    spec = {
        "entry": "x",
        "nodes": [{"id": "x", "type": "unknown_type"}],
        "edges": [],
    }
    with pytest.raises(ValueError, match="Unknown node type"):
        build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])


def test_build_graph_router_without_condition_raises():
    spec = {
        "entry": "r",
        "nodes": [{"id": "r", "type": "router"}],
        "edges": [],
    }
    with pytest.raises(ValueError, match="condition"):
        build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])


def test_build_graph_lambda_without_fn_raises():
    spec = {
        "entry": "l",
        "nodes": [{"id": "l", "type": "lambda"}],
        "edges": [],
    }
    with pytest.raises(ValueError, match="fn"):
        build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])


def test_build_graph_edge_without_to_or_mapping_raises():
    spec = {
        "entry": "a",
        "nodes": [{"id": "a", "type": "lambda", "fn": "lambda s: {}"}],
        "edges": [{"from": "a"}],  # missing 'to' and 'mapping'
    }
    with pytest.raises(ValueError, match="must have either"):
        build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])


# ---------------------------------------------------------------------------
# build_graph_from_spec — agent node tool filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_node_no_tools_key_gets_all_tools():
    """When 'tools' key is absent, agent receives all tool schemas (verified via LLM call)."""
    all_schemas = [{"name": "web_search"}, {"name": "calculator"}]
    spec = {
        "entry": "agent",
        "state_schema": {
            "messages": {"reducer": "append", "default": []},
            "tool_calls": {"reducer": "replace", "default": []},
            "final_answer": {"reducer": "replace", "default": ""},
            "token_count": {"reducer": "add", "default": 0},
        },
        "nodes": [{"id": "agent", "type": "agent", "system_prompt": ""}],
        "edges": [{"from": "agent", "to": "__end__"}],
    }
    llm = MockLLM([_MockResponse("all tools response", "end_turn")])
    compiled = build_graph_from_spec(spec, llm=llm, executor=None, tool_schemas=all_schemas)
    await compiled.invoke({"messages": [{"role": "user", "content": "hi"}]})
    # All tool schemas passed to LLM (both schemas visible)
    call = llm.calls[0]
    assert call["tools"] is not None
    assert len(call["tools"]) == 2


def test_agent_node_empty_tools_list_gets_none():
    """When tools: [] is set, AgentNode gets no tools (pure LLM pass)."""
    spec = {
        "entry": "agent",
        "nodes": [{"id": "agent", "type": "agent", "system_prompt": "", "tools": []}],
        "edges": [],
    }
    compiled = build_graph_from_spec(
        spec, llm=MockLLM(), executor=None,
        tool_schemas=[{"name": "web_search"}]
    )
    assert compiled is not None


def test_agent_node_allowlist_filters_tools():
    """When tools: [name] is set, only matching schemas are passed."""
    spec = {
        "entry": "agent",
        "nodes": [{"id": "agent", "type": "agent", "system_prompt": "", "tools": ["web_search"]}],
        "edges": [],
    }
    compiled = build_graph_from_spec(
        spec, llm=MockLLM(), executor=None,
        tool_schemas=[{"name": "web_search"}, {"name": "calculator"}]
    )
    assert compiled is not None


# ---------------------------------------------------------------------------
# build_graph_from_spec — state schema
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_schema_reducers_applied():
    spec = {
        "entry": "node_a",
        "state_schema": {
            "count": {"reducer": "add", "default": 0},
            "tags": {"reducer": "append", "default": []},
        },
        "nodes": [
            {"id": "node_a", "type": "lambda", "fn": "lambda s: {'count': 5, 'tags': ['a']}"},
            {"id": "node_b", "type": "lambda", "fn": "lambda s: {'count': 3, 'tags': ['b']}"},
        ],
        "edges": [
            {"from": "node_a", "to": "node_b"},
            {"from": "node_b", "to": "__end__"},
        ],
    }
    compiled = build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])
    state = await compiled.invoke()
    assert state["count"] == 8         # add reducer
    assert state["tags"] == ["a", "b"] # append reducer


# ---------------------------------------------------------------------------
# build_graph_from_spec — router + conditional edges
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_router_conditional_edge():
    spec = {
        "entry": "setter",
        "nodes": [
            {"id": "setter",   "type": "lambda", "fn": "lambda s: {'flag': True}"},
            {"id": "router",   "type": "router",
             "condition": "lambda state: 'yes' if state.get('flag') else 'no'"},
            {"id": "yes_node", "type": "lambda", "fn": "lambda s: {'result': 'yes'}"},
            {"id": "no_node",  "type": "lambda", "fn": "lambda s: {'result': 'no'}"},
        ],
        "edges": [
            {"from": "setter", "to": "router"},
            {"from": "router", "mapping": {"yes": "yes_node", "no": "no_node"}},
            {"from": "yes_node", "to": "__end__"},
            {"from": "no_node",  "to": "__end__"},
        ],
    }
    compiled = build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])
    state = await compiled.invoke()
    assert state["result"] == "yes"


@pytest.mark.asyncio
async def test_inline_condition_on_non_router_node():
    """Conditional edge with explicit 'condition' key (not a router node)."""
    spec = {
        "entry": "start",
        "nodes": [
            {"id": "start",  "type": "lambda", "fn": "lambda s: {'x': 1}"},
            {"id": "branch_a", "type": "lambda", "fn": "lambda s: {'result': 'a'}"},
            {"id": "branch_b", "type": "lambda", "fn": "lambda s: {'result': 'b'}"},
        ],
        "edges": [
            {
                "from": "start",
                "condition": "lambda state: 'a' if state.get('x') else 'b'",
                "mapping": {"a": "branch_a", "b": "branch_b"},
            },
            {"from": "branch_a", "to": "__end__"},
            {"from": "branch_b", "to": "__end__"},
        ],
    }
    compiled = build_graph_from_spec(spec, llm=None, executor=None, tool_schemas=[])
    state = await compiled.invoke()
    assert state["result"] == "a"


# ---------------------------------------------------------------------------
# run_graph — envelope integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_graph_seeds_user_message_from_envelope():
    """run_graph extracts payload.text from envelope and seeds state['messages']."""
    spec = {
        "entry": "capture",
        "nodes": [
            {"id": "capture", "type": "lambda",
             "fn": "lambda s: {'captured': s.get('messages', [{}])[0].get('content', '')}"},
        ],
        "edges": [{"from": "capture", "to": "__end__"}],
    }
    envelope = {
        "id": "trig_001",
        "payload": {"text": "What is AI?"},
    }
    state = await run_graph(spec, envelope, llm=None, executor=None, tool_schemas=[])
    assert state["captured"] == "What is AI?"


@pytest.mark.asyncio
async def test_run_graph_respects_max_steps():
    """run_graph passes max_steps through to compiled.invoke()."""
    spec = {
        "entry": "loop",
        "max_steps": 3,
        "nodes": [
            {"id": "loop", "type": "lambda", "fn": "lambda s: {'n': s.get('n', 0) + 1}"},
        ],
        "edges": [{"from": "loop", "to": "loop"}],  # infinite cycle
    }
    envelope = {"id": "t1", "payload": {"text": ""}}
    state = await run_graph(spec, envelope, llm=None, executor=None, tool_schemas=[])
    assert state["__max_steps_reached__"] is True
    assert state["__steps__"] == 3


@pytest.mark.asyncio
async def test_run_graph_returns_final_answer():
    spec = {
        "entry": "answer",
        "nodes": [
            {"id": "answer", "type": "lambda",
             "fn": "lambda s: {'final_answer': 'The answer is 42'}"},
        ],
        "edges": [{"from": "answer", "to": "__end__"}],
    }
    envelope = {"id": "t2", "payload": {"text": "What is 6*7?"}}
    state = await run_graph(spec, envelope, llm=None, executor=None, tool_schemas=[])
    assert state["final_answer"] == "The answer is 42"


# ---------------------------------------------------------------------------
# run_graph — with agent node and mock LLM
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_graph_agent_node_end_turn():
    spec = _agent_spec()
    envelope = {"id": "t3", "payload": {"text": "Hello"}}
    llm = MockLLM([_MockResponse("Hello back!", "end_turn")])
    state = await run_graph(spec, envelope, llm=llm, executor=MockExecutor(), tool_schemas=[])
    assert state["final_answer"] == "Hello back!"
    assert state["token_count"] == 15  # 5 input + 10 output


# ---------------------------------------------------------------------------
# run_graph — final_answer fallback for a custom output_key
# ---------------------------------------------------------------------------
#
# Found live: a graph whose terminal agent node used output_key: "summary"
# (a real, supported field — _build_node reads it straight off the node
# spec) produced a correct answer that agent_runtime/main.py still reported
# as "Graph completed but produced no final_answer." — main.py only ever
# reads the literal "final_answer" key. run_graph() now falls back to
# whatever the last-executed agent node actually wrote, under its own
# configured output_key, when "final_answer" itself is empty.

@pytest.mark.asyncio
async def test_run_graph_falls_back_to_last_agent_nodes_output_key():
    spec = _agent_spec(output_key="summary")
    envelope = {"id": "t4", "payload": {"text": "Hello"}}
    llm = MockLLM([_MockResponse("Here is the summary.", "end_turn")])
    state = await run_graph(spec, envelope, llm=llm, executor=MockExecutor(), tool_schemas=[])
    assert state["summary"] == "Here is the summary."
    assert state["final_answer"] == "Here is the summary."


@pytest.mark.asyncio
async def test_run_graph_fallback_uses_the_last_node_not_an_earlier_one():
    """Two agent nodes in sequence, each with its own output_key — only the
    LAST one's output should be used as the fallback, not the first."""
    spec = {
        "entry": "draft",
        "state_schema": {
            "messages": {"reducer": "append", "default": []},
            "tool_calls": {"reducer": "replace", "default": []},
            "final_answer": {"reducer": "replace", "default": ""},
            "token_count": {"reducer": "add", "default": 0},
            "draft_text": {"reducer": "replace", "default": ""},
            "polished_text": {"reducer": "replace", "default": ""},
        },
        "nodes": [
            {"id": "draft", "type": "agent", "output_key": "draft_text"},
            {"id": "polish", "type": "agent", "output_key": "polished_text"},
        ],
        "edges": [
            {"from": "draft", "to": "polish"},
            {"from": "polish", "to": "__end__"},
        ],
    }
    envelope = {"id": "t5", "payload": {"text": "Write something"}}
    llm = MockLLM([
        _MockResponse("rough draft", "end_turn"),
        _MockResponse("polished version", "end_turn"),
    ])
    state = await run_graph(spec, envelope, llm=llm, executor=MockExecutor(), tool_schemas=[])
    assert state["final_answer"] == "polished version"


@pytest.mark.asyncio
async def test_run_graph_explicit_final_answer_is_not_overridden():
    """A node that already writes final_answer (the default output_key)
    keeps working exactly as before — the fallback only kicks in when
    final_answer itself is empty."""
    state = await run_graph(_agent_spec(), {"id": "t6", "payload": {"text": "hi"}},
                             llm=MockLLM([_MockResponse("direct answer", "end_turn")]),
                             executor=MockExecutor(), tool_schemas=[])
    assert state["final_answer"] == "direct answer"


@pytest.mark.asyncio
async def test_run_graph_no_fallback_when_last_node_is_not_an_agent():
    """A lambda or router node ending the graph has no output_key concept
    at all — the fallback must not invent one; final_answer stays empty."""
    spec = {
        "entry": "transform",
        "nodes": [
            {"id": "transform", "type": "lambda", "fn": "lambda s: {'note': 'done'}"},
        ],
        "edges": [{"from": "transform", "to": "__end__"}],
    }
    state = await run_graph(spec, {"id": "t7", "payload": {"text": "hi"}},
                             llm=None, executor=None, tool_schemas=[])
    assert state.get("final_answer", "") == ""


# ---------------------------------------------------------------------------
# _TracingNode — span recording
# ---------------------------------------------------------------------------

class MockTraceStore:
    def __init__(self):
        self.spans_started = []
        self.spans_finished = []

    def start_span(self, trace_id, name, parent_span_id=None, input=None, attributes=None):
        sid = f"span_{len(self.spans_started)}"
        self.spans_started.append({"trace_id": trace_id, "name": name, "parent_span_id": parent_span_id})
        return sid

    def finish_span(self, span_id, output=None, attributes=None, status="ok", error=None):
        self.spans_finished.append({"span_id": span_id, "status": status, "error": error})


@pytest.mark.asyncio
async def test_tracing_node_records_span():
    trace_store = MockTraceStore()
    spec = {
        "entry": "node",
        "nodes": [
            {"id": "node", "type": "lambda", "fn": "lambda s: {'x': 1}"},
        ],
        "edges": [{"from": "node", "to": "__end__"}],
    }
    compiled = build_graph_from_spec(
        spec, llm=None, executor=None, tool_schemas=[],
        trace_store=trace_store, trace_id="trace_abc", parent_span_id="span_root",
    )
    await compiled.invoke()
    assert len(trace_store.spans_started) == 1
    assert trace_store.spans_started[0]["name"] == "graph.lambda"
    assert trace_store.spans_started[0]["parent_span_id"] == "span_root"


@pytest.mark.asyncio
async def test_tracing_node_records_error_span():
    trace_store = MockTraceStore()

    def _boom(state):
        raise RuntimeError("intentional failure")

    spec = {
        "entry": "bad",
        "nodes": [{"id": "bad", "type": "lambda", "fn": "lambda s: 1/0"}],
        "edges": [],
    }
    compiled = build_graph_from_spec(
        spec, llm=None, executor=None, tool_schemas=[],
        trace_store=trace_store, trace_id="trace_err", parent_span_id="span_root",
    )
    with pytest.raises(Exception):
        await compiled.invoke()

    finished = trace_store.spans_finished
    assert any(s["status"] == "error" for s in finished)


@pytest.mark.asyncio
async def test_tracing_node_agent_span_name():
    """AgentNode gets span name 'llm.call'."""
    from agentix.agent_runtime.graph_runner import _TracingNode
    from agentix.graph.nodes import AgentNode

    trace_store = MockTraceStore()
    inner = AgentNode("ag", MockLLM([_MockResponse()]), output_key="final_answer")
    node = _TracingNode(
        inner=inner,
        trace_store=trace_store,
        trace_id="trace_x",
        parent_span_id="span_p",
        node_type="agent",
    )
    state = {
        "messages": [{"role": "user", "content": "hi"}],
        "tool_calls": [],
        "token_count": 0,
    }
    await node.run(state)
    assert trace_store.spans_started[0]["name"] == "llm.call"
