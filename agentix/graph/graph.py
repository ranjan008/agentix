"""
StateGraph — LangGraph-equivalent directed graph for agent orchestration.

Nodes are connected by edges (unconditional) or conditional edges (routing
based on node output).  Special sentinel names:
  "__start__"  — entry point (added automatically)
  "__end__"    — terminal node; execution stops here

Example:

    graph = StateGraph(schema)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry("agent")
    graph.add_conditional_edge(
        "agent",
        condition=lambda s: "tools" if s.get("tool_calls") else "__end__",
        mapping={"tools": "tools", "__end__": "__end__"},
    )
    graph.add_edge("tools", "agent")
    runner = graph.compile()
    final_state = await runner.invoke(initial_state)
"""
from __future__ import annotations

import logging
from typing import Callable, Any

from agentix.graph.state import StateSchema
from agentix.graph.nodes import BaseNode

logger = logging.getLogger(__name__)

START = "__start__"
END   = "__end__"


class _Edge:
    def __init__(self, src: str, dst: str) -> None:
        self.src = src
        self.dst = dst


class _ConditionalEdge:
    def __init__(self, src: str, condition: Callable[[dict], str], mapping: dict[str, str] | None) -> None:
        self.src = src
        self.condition = condition
        self.mapping = mapping  # None = identity (condition returns node name directly)


class StateGraph:
    """
    Directed graph where each node transforms shared state.

    Build → compile → invoke pattern (same as LangGraph's interface).
    """

    def __init__(self, schema: StateSchema | None = None) -> None:
        self._schema = schema
        self._nodes: dict[str, BaseNode] = {}
        self._edges: list[_Edge] = []
        self._cond_edges: list[_ConditionalEdge] = []
        self._entry: str | None = None

    # ------------------------------------------------------------------
    # Graph construction API
    # ------------------------------------------------------------------

    def add_node(self, name: str, node: BaseNode) -> "StateGraph":
        if name in (START, END):
            raise ValueError(f"'{name}' is a reserved node name")
        self._nodes[name] = node
        return self

    def set_entry(self, node_name: str) -> "StateGraph":
        self._entry = node_name
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self._edges.append(_Edge(src, dst))
        return self

    def add_conditional_edge(
        self,
        src: str,
        condition: Callable[[dict], str],
        mapping: dict[str, str] | None = None,
    ) -> "StateGraph":
        """
        After `src` runs, call `condition(state)` to get a key.
        If `mapping` is provided: next_node = mapping[key].
        If `mapping` is None: key is used as the next node name directly.
        """
        self._cond_edges.append(_ConditionalEdge(src, condition, mapping))
        return self

    def compile(self) -> "CompiledGraph":
        if not self._entry:
            raise ValueError("No entry point set. Call set_entry() first.")
        return CompiledGraph(
            schema=self._schema,
            nodes=self._nodes,
            edges=self._edges,
            cond_edges=self._cond_edges,
            entry=self._entry,
        )


class CompiledGraph:
    """
    Executable form of a StateGraph.  Call invoke() to run.
    """

    def __init__(
        self,
        schema: StateSchema | None,
        nodes: dict[str, BaseNode],
        edges: list[_Edge],
        cond_edges: list[_ConditionalEdge],
        entry: str,
    ) -> None:
        self._schema = schema
        self._nodes = nodes
        self._entry = entry

        # Build adjacency: node → list of unconditional successors
        self._next: dict[str, list[str]] = {name: [] for name in nodes}
        for e in edges:
            self._next.setdefault(e.src, []).append(e.dst)

        # Build conditional adjacency: node → condition
        self._cond: dict[str, _ConditionalEdge] = {}
        for ce in cond_edges:
            self._cond[ce.src] = ce

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def invoke(
        self,
        initial_state: dict[str, Any] | None = None,
        max_steps: int = 50,
    ) -> dict[str, Any]:
        """
        Run the graph to completion.

        Starts at the entry node, applies state updates, and follows edges
        until reaching END or exceeding max_steps.

        Returns the final state.
        """
        state: dict[str, Any]
        if self._schema:
            state = self._schema.initial()
            if initial_state:
                state = self._schema.update(state, initial_state)
        else:
            state = dict(initial_state or {})

        current: str | None = self._entry
        steps = 0

        while current not in (None, END) and steps < max_steps:
            assert current is not None  # while condition guarantees this; assert narrows for mypy
            steps += 1
            node = self._nodes.get(current)
            if not node:
                raise ValueError(f"Node '{current}' is referenced but not registered")

            logger.debug("Graph step %d: node=%s", steps, current)
            patch = await node.run(state)

            if self._schema:
                state = self._schema.update(state, patch)
            else:
                state = {**state, **patch}

            current = self._resolve_next(current, state)

        if steps >= max_steps:
            logger.warning("Graph reached max_steps=%d without reaching __end__", max_steps)
            state["__max_steps_reached__"] = True

        state["__steps__"] = steps
        return state

    def _resolve_next(self, current: str, state: dict[str, Any]) -> str | None:
        # Conditional edge takes priority
        if current in self._cond:
            ce = self._cond[current]
            key = ce.condition(state)
            if ce.mapping:
                return ce.mapping.get(key, END)
            return key  # condition returns node name directly

        # Unconditional edges
        nexts = self._next.get(current, [])
        if not nexts:
            return None  # no outgoing edge → done
        if len(nexts) == 1:
            return nexts[0]
        raise ValueError(
            f"Node '{current}' has {len(nexts)} unconditional outgoing edges. "
            "Use add_conditional_edge for branching."
        )
