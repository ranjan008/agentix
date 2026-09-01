"""
Graph nodes — the building blocks of a StateGraph.

Node types:
  AgentNode  — calls an LLM with the current state as context; returns state patch
  ToolNode   — executes a tool call found in state["tool_calls"]
  RouterNode — evaluates a condition function to decide the next edge
  LambdaNode — wraps a plain coroutine function as a node

All nodes are async. sync functions are wrapped automatically.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BaseNode:
    """Abstract base for all graph nodes."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the node; return a state patch dict."""
        raise NotImplementedError


class LambdaNode(BaseNode):
    """
    Wraps a plain function or coroutine as a node.

    The function receives the current state and returns a patch dict.

    Example:
        node = LambdaNode("greet", lambda state: {"messages": [{"role": "assistant", "content": "Hello!"}]})
    """

    def __init__(self, name: str, fn: Callable) -> None:
        super().__init__(name)
        self._fn = fn

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        if inspect.iscoroutinefunction(self._fn):
            result = await self._fn(state)
        else:
            result = self._fn(state)
        return result or {}


class AgentNode(BaseNode):
    """
    Calls an LLM (via LLMRouter) using state["messages"] as the conversation.

    On tool_use stop reason, appends tool calls to state["tool_calls"].
    On end_turn, sets state["final_answer"] (configurable via output_key).
    """

    def __init__(
        self,
        name: str,
        llm: Any,
        system_prompt: str = "",
        tools: list[dict] | None = None,
        output_key: str = "final_answer",
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int = 4096,
        messages_key: str = "messages",
    ) -> None:
        super().__init__(name)
        self._llm = llm
        self._system = system_prompt
        self._tools = tools
        self._output_key = output_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._messages_key = messages_key

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        messages = list(state.get(self._messages_key, []))

        response = await self._llm.complete(
            messages=messages,
            system=self._system,
            tools=self._tools,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        patch: dict[str, Any] = {}

        if response.stop_reason in ("end_turn", "stop"):
            patch[self._output_key] = response.content
            patch["messages"] = [{"role": "assistant", "content": response.content}]
            patch["stop_reason"] = "end_turn"

        elif response.stop_reason == "tool_use" and response.tool_calls:
            raw_blocks = None
            if isinstance(response.raw, dict):
                raw_blocks = response.raw.get("blocks")
            if raw_blocks:
                assistant_content = raw_blocks
            else:
                # raw["blocks"] is only populated by AnthropicProvider
                # (agentix/llm/providers/anthropic_provider.py) — every
                # other provider (GeminiProvider, the OpenAI-compatible
                # local_provider.py backing the ollama/lmstudio/vllm/local
                # aliases, openai_provider.py) returns `raw` as its own
                # SDK response object instead, so `isinstance(raw, dict)`
                # is False and this used to fall straight through to
                # `response.content` — plain text with the tool_use block
                # dropped entirely. The very next turn's tool_result then
                # referenced a tool_use_id that didn't exist in any prior
                # message, and Anthropic's API rejected it outright
                # ("unexpected tool_use_id found in tool_result blocks").
                # Since `default_provider: gemini` is tried before
                # `anthropic` in every configured fallback chain, this
                # broke tool-calling graph loops on effectively every real
                # run, not just as an edge case. Fix: build the content
                # blocks directly from `response.tool_calls`, which every
                # BaseLLMProvider populates uniformly regardless of which
                # provider actually served the call.
                assistant_content = []
                if response.content:
                    assistant_content.append({"type": "text", "text": response.content})
                assistant_content.extend(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in response.tool_calls
                )
            patch["messages"] = [{"role": "assistant", "content": assistant_content}]
            patch["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "input": tc.input}
                for tc in response.tool_calls
            ]
            patch["stop_reason"] = "tool_use"

        patch["token_count"] = response.input_tokens + response.output_tokens

        logger.debug("AgentNode '%s': stop_reason=%s", self.name, response.stop_reason)
        return patch


class ToolNode(BaseNode):
    """
    Executes tool calls found in state["tool_calls"] using a ToolExecutor.

    Appends tool_result blocks to state["messages"] and clears state["tool_calls"].
    """

    def __init__(self, name: str, executor: Any) -> None:
        super().__init__(name)
        self._executor = executor

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        tool_calls: list[dict] = state.get("tool_calls", [])
        if not tool_calls:
            return {}

        tool_results = []
        for tc in tool_calls:
            try:
                result = await self._executor.execute(tc["name"], tc["input"])
                result_str = json.dumps(result) if not isinstance(result, str) else result
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": result_str,
                })
                logger.debug("ToolNode '%s': executed %s → %d chars", self.name, tc["name"], len(result_str))
            except Exception as exc:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": f"Error: {exc}",
                    "is_error": True,
                })
                logger.warning("ToolNode '%s': %s failed: %s", self.name, tc["name"], exc)

        return {
            "messages": [{"role": "user", "content": tool_results}],
            "tool_calls": [],  # clear after execution
        }


class RouterNode(BaseNode):
    """
    Evaluates a condition function against the current state to determine
    which edge to follow next.  Does NOT modify state — purely for routing.

    The condition function returns a string key that the graph's conditional
    edge map uses to pick the next node.

    Example:
        router = RouterNode("router", condition=lambda s: "tools" if s.get("tool_calls") else "end")
    """

    def __init__(self, name: str, condition: Callable[[dict], str]) -> None:
        super().__init__(name)
        self._condition = condition

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        return {}  # Router doesn't change state

    def route(self, state: dict[str, Any]) -> str:
        """Return the next node name based on current state."""
        if inspect.iscoroutinefunction(self._condition):
            raise TypeError("RouterNode condition must be synchronous")
        return self._condition(state)
