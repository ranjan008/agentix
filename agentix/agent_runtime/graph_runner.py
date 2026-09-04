"""
Graph runner — builds a CompiledGraph from an agent spec's `graph:` block
and executes it for a given trigger envelope.

IMPORTANT — the "final_answer" convention: whatever ends up in
state["final_answer"] when the graph reaches __end__ is what
agent_runtime/main.py sends back as the reply text on every channel. This
is true regardless of state_schema — state_schema only configures the
*reducer* a key uses when patched, it doesn't make a key special. What
makes "final_answer" special is entirely that main.py's own post-processing
reads that one literal key name; nothing enforces an agent actually writing
it. A graph is free to use a different output_key (see `nodes:` below) —
run_graph() falls back to whatever the last-executed agent node wrote under
its own output_key if "final_answer" itself is empty, so this doesn't
silently produce an empty reply, but a graph that always wants its answer
under a specific name should still just set output_key: final_answer
explicitly rather than relying on the fallback.

YAML graph schema:

  graph:
    max_steps: 30            # optional, default 50

    state_schema:            # optional typed state fields
      messages:
        reducer: append      # replace | append | merge | add
        default: []
      tool_calls:
        reducer: replace
        default: []
      final_answer:
        reducer: replace
        default: ""
      token_count:
        reducer: add
        default: 0

    nodes:
      - id: my_agent
        type: agent          # agent | tool | router | lambda
        system_prompt: "..."
        model: claude-haiku-4-5-20251001
        temperature: 0.3
        max_tokens: 2048
        output_key: final_answer   # state key to store LLM text response
        tools: [web_search]        # optional allowlist; omit for all tools

      - id: tool_executor
        type: tool            # runs state["tool_calls"] via ToolExecutor

      - id: my_router
        type: router
        condition: "lambda state: 'tools' if state.get('tool_calls') else 'done'"

      - id: my_transform
        type: lambda
        fn: "lambda state: {'final_answer': state.get('final_answer', '').upper()}"

    edges:
      # Unconditional edge
      - from: my_agent
        to: my_router

      # Conditional edge — router node: condition taken from the node itself
      - from: my_router
        mapping:
          tools: tool_executor
          done: __end__

      # Conditional edge — inline condition on a non-router node
      - from: some_node
        condition: "lambda state: 'a' if state['x'] else 'b'"
        mapping:
          a: node_a
          b: node_b

      # Loop back
      - from: tool_executor
        to: my_agent

    entry: my_agent
"""
from __future__ import annotations

import builtins as _builtins_mod
import logging
from typing import Any

from agentix.graph.graph import StateGraph
from agentix.graph.nodes import AgentNode, BaseNode, LambdaNode, RouterNode, ToolNode
from agentix.graph.state import FieldSchema, StateSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tracing wrapper
# ---------------------------------------------------------------------------

# Span name by node type — matches the conventions used in the agentic loop
_SPAN_NAME: dict[type, str] = {
    AgentNode:  "llm.call",
    ToolNode:   "tool.call",
    RouterNode: "graph.router",
    LambdaNode: "graph.lambda",
}


class _TracingNode(BaseNode):
    """Wraps any BaseNode and records a trace span around each execution."""

    def __init__(
        self,
        inner: BaseNode,
        trace_store: Any,
        trace_id: str,
        parent_span_id: str,
        node_type: str,
    ) -> None:
        super().__init__(inner.name)
        self._inner = inner
        self._ts = trace_store
        self._trace_id = trace_id
        self._parent_span_id = parent_span_id
        self._span_name = _SPAN_NAME.get(type(inner), f"graph.{node_type}")
        self._node_type = node_type

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        attrs: dict = {"node_name": self.name, "node_type": self._node_type}

        span_id = self._ts.start_span(
            self._trace_id,
            self._span_name,
            parent_span_id=self._parent_span_id,
            input={"node": self.name, "messages_count": len(state.get("messages", []))},
            attributes=attrs,
        )
        try:
            patch = await self._inner.run(state)
            out_attrs = dict(attrs)
            # Enrich span output with useful per-type details
            if self._node_type == "agent":
                out_attrs["stop_reason"] = patch.get("stop_reason", "")
                out_attrs["input_tokens"] = state.get("token_count", 0)  # before
                out_attrs["output_tokens"] = patch.get("token_count", 0)
            elif self._node_type == "tool":
                out_attrs["tool_count"] = len(state.get("tool_calls", []))
            elif self._node_type == "router":
                out_attrs["patch_keys"] = list(patch.keys())
            self._ts.finish_span(
                span_id,
                output={"patch_keys": list(patch.keys()), "stop_reason": patch.get("stop_reason")},
                attributes=out_attrs,
            )
            return patch
        except Exception as exc:
            self._ts.finish_span(span_id, status="error", error=str(exc), attributes=attrs)
            raise

# Eval namespace — safe subset of builtins (no exec/open/import/__import__)
_SAFE_BUILTINS = {
    name: getattr(_builtins_mod, name)
    for name in (
        "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
        "range", "enumerate", "zip", "map", "filter", "any", "all",
        "min", "max", "sum", "abs", "round", "sorted", "reversed",
        "isinstance", "issubclass", "type", "repr", "print",
    )
}
_EVAL_NS: dict = {"__builtins__": _SAFE_BUILTINS}


def _eval_fn(expr: str) -> Any:
    """Evaluate a lambda or function expression from YAML."""
    try:
        return eval(expr.strip(), _EVAL_NS)  # noqa: S307
    except Exception as exc:
        raise ValueError(f"Failed to evaluate graph expression {expr!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------

def _build_state_schema(schema_spec: dict) -> StateSchema:
    fields: dict[str, FieldSchema] = {}
    for name, cfg in schema_spec.items():
        if isinstance(cfg, dict):
            reducer = cfg.get("reducer", "replace")
            default = cfg.get("default", None)
        else:
            reducer = "replace"
            default = None
        fields[name] = FieldSchema(default=default, reducer=reducer)
    return StateSchema(fields)


# ---------------------------------------------------------------------------
# Node builder
# ---------------------------------------------------------------------------

def _build_node(node_spec: dict, llm: Any, executor: Any, all_tool_schemas: list[dict]):
    node_id = node_spec["id"]
    node_type = node_spec.get("type", "agent")

    if node_type == "agent":
        # Optionally restrict which tools this node can call.
        # tools key absent → all tools available
        # tools: []        → explicitly no tools (pure LLM pass)
        # tools: [name]    → allowlist
        _tools_key = "tools"
        if _tools_key not in node_spec:
            tools = all_tool_schemas or None   # all tools
        elif not node_spec[_tools_key]:
            tools = None                       # empty list → no tools
        else:
            requested = node_spec[_tools_key]
            tools = [s for s in all_tool_schemas if s["name"] in requested]
            # A requested name that matches nothing used to silently
            # collapse into `tools or None` — indistinguishable from
            # "no tools intended". Found live: a node with tools:
            # ["notion"] (a connector bare name, not that connector's
            # real per-action tool names) got tools=None this way, the
            # LLM had nothing to call, and it narrated having performed
            # the action instead — the graph reported success with no
            # tool ever invoked. A node that explicitly asks for specific
            # tools and gets none of them is a misconfiguration, not an
            # intentional "no tools" — fail loudly at build time instead
            # of silently letting the model fabricate a result.
            matched = {s["name"] for s in tools}
            unmatched = [n for n in requested if n not in matched]
            if unmatched:
                available = sorted(s["name"] for s in all_tool_schemas)
                raise ValueError(
                    f"Agent node '{node_id}' requests tool(s) {unmatched} not available to "
                    f"this agent (loaded: {available}) — check the connector/skill is granted "
                    "at the agent level and that the name matches its real tool name exactly."
                )
            tools = tools or None

        return AgentNode(
            name=node_id,
            llm=llm,
            system_prompt=node_spec.get("system_prompt", ""),
            tools=tools,
            output_key=node_spec.get("output_key", "final_answer"),
            model=node_spec.get("model"),
            temperature=float(node_spec.get("temperature", 1.0)),
            max_tokens=int(node_spec.get("max_tokens", 4096)),
            messages_key=node_spec.get("messages_key", "messages"),
        )

    if node_type == "tool":
        return ToolNode(name=node_id, executor=executor)

    if node_type == "router":
        condition_expr = node_spec.get("condition")
        if not condition_expr:
            raise ValueError(f"Router node '{node_id}' requires a 'condition' expression")
        return RouterNode(name=node_id, condition=_eval_fn(condition_expr))

    if node_type == "lambda":
        fn_expr = node_spec.get("fn")
        if not fn_expr:
            raise ValueError(f"Lambda node '{node_id}' requires a 'fn' expression")
        return LambdaNode(name=node_id, fn=_eval_fn(fn_expr))

    raise ValueError(f"Unknown node type '{node_type}' for node '{node_id}'")


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph_from_spec(
    graph_spec: dict,
    llm: Any,
    executor: Any,
    tool_schemas: list[dict],
    trace_store: Any = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> Any:  # CompiledGraph
    """Build and compile a StateGraph from the `graph:` block of an agent YAML."""

    # State schema (optional)
    schema_spec = graph_spec.get("state_schema", {})
    schema = _build_state_schema(schema_spec) if schema_spec else None

    # Build nodes
    node_specs = graph_spec.get("nodes", [])
    router_conditions: dict[str, Any] = {}
    graph = StateGraph(schema)

    for node_spec in node_specs:
        node = _build_node(node_spec, llm, executor, tool_schemas)
        # Wrap with tracing if a trace store is provided
        if trace_store and trace_id and parent_span_id:
            node = _TracingNode(
                inner=node,
                trace_store=trace_store,
                trace_id=trace_id,
                parent_span_id=parent_span_id,
                node_type=node_spec.get("type", "agent"),
            )
        graph.add_node(node_spec["id"], node)
        if node_spec.get("type") == "router":
            router_conditions[node_spec["id"]] = _eval_fn(node_spec["condition"])

    # Entry point
    entry = graph_spec.get("entry")
    if not entry:
        raise ValueError("Graph spec must define 'entry' node")
    graph.set_entry(entry)

    # Wire edges
    for edge_spec in graph_spec.get("edges", []):
        src = edge_spec["from"]

        if "mapping" in edge_spec:
            # Conditional edge — condition comes from router node or inline 'condition' field
            if "condition" in edge_spec:
                condition = _eval_fn(edge_spec["condition"])
            elif src in router_conditions:
                condition = router_conditions[src]
            else:
                raise ValueError(
                    f"Edge from '{src}' has a mapping but no condition. "
                    "Add a 'condition' key or make the source node type: router."
                )
            graph.add_conditional_edge(src, condition, edge_spec["mapping"])

        elif "to" in edge_spec:
            graph.add_edge(src, edge_spec["to"])

        else:
            raise ValueError(f"Edge from '{src}' must have either 'to' or 'mapping'")

    return graph.compile()


# ---------------------------------------------------------------------------
# High-level runner
# ---------------------------------------------------------------------------

async def run_graph(
    graph_spec: dict,
    envelope: dict,
    llm: Any,
    executor: Any,
    tool_schemas: list[dict],
    trace_store: Any = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
) -> dict:
    """
    Build the graph from spec, seed initial state from the trigger envelope,
    and run it to completion.  Returns the final state dict.
    """
    compiled = build_graph_from_spec(
        graph_spec, llm, executor, tool_schemas,
        trace_store=trace_store, trace_id=trace_id, parent_span_id=parent_span_id,
    )

    user_text = envelope.get("payload", {}).get("text", "")
    initial_state = {
        "messages": [{"role": "user", "content": user_text}],
        "tool_calls": [],
        "final_answer": "",
        "token_count": 0,
    }

    max_steps = int(graph_spec.get("max_steps", 50))
    logger.info("Running graph: entry=%s max_steps=%d", graph_spec.get("entry"), max_steps)

    final_state = await compiled.invoke(initial_state, max_steps=max_steps)

    steps = final_state.get("__steps__", 0)
    capped = final_state.get("__max_steps_reached__", False)
    logger.info("Graph finished: steps=%d max_steps_reached=%s", steps, capped)

    # "final_answer" is an undocumented convention every caller of run_graph
    # (agent_runtime/main.py) reads as the reply text — a graph whose last
    # node writes somewhere else via a custom output_key (a real, supported
    # feature: _build_node above reads output_key straight off the node
    # spec) produced a perfectly valid answer that the caller would report
    # as "no final_answer" anyway. Fall back to whatever the actually-last-
    # executed agent node wrote under ITS OWN output_key, rather than
    # silently discarding a real result just because it wasn't named the
    # default. Only applies when "final_answer" itself is empty — a node
    # that explicitly opts into writing final_answer (the default) keeps
    # working exactly as before.
    if not final_state.get("final_answer"):
        node_output_keys = {
            spec["id"]: spec.get("output_key", "final_answer")
            for spec in graph_spec.get("nodes", [])
            if spec.get("type") == "agent"
        }
        fallback_key = node_output_keys.get(final_state.get("__last_node__"))
        if fallback_key and final_state.get(fallback_key):
            final_state["final_answer"] = final_state[fallback_key]

    return final_state
