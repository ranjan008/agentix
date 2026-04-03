"""
MockLLMProvider — deterministic LLM for agent unit tests.

Usage:

  script = LLMScript([
      # Turn 1: assistant calls a tool
      LLMTurn(
          tool_calls=[ToolCall(id="tc1", name="web_search", input={"query": "test"})],
          stop_reason="tool_use",
      ),
      # Turn 2: assistant gives final answer
      LLMTurn(content="Here are the results!", stop_reason="end_turn"),
  ])

  provider = MockLLMProvider(script)

The provider replays turns in order.  If all turns are consumed and more
complete() calls are made, it raises LLMScriptExhausted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class LLMScriptExhausted(Exception):
    """Raised when MockLLMProvider.complete() is called beyond the script length."""


@dataclass
class LLMTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    model: str = "mock"
    input_tokens: int = 10
    output_tokens: int = 20


class LLMScript:
    """Ordered sequence of LLMTurns to replay."""

    def __init__(self, turns: list[LLMTurn]) -> None:
        self._turns = list(turns)
        self._index = 0

    def next_turn(self) -> LLMTurn:
        if self._index >= len(self._turns):
            raise LLMScriptExhausted(
                f"MockLLMProvider script exhausted after {self._index} turn(s). "
                "Add more LLMTurns or check your agent loop."
            )
        turn = self._turns[self._index]
        self._index += 1
        return turn

    def reset(self) -> None:
        self._index = 0

    @property
    def turns_consumed(self) -> int:
        return self._index

    @property
    def turns_remaining(self) -> int:
        return len(self._turns) - self._index


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic LLM provider that replays a pre-defined script.

    Can be used with LLMRouter by registering under a custom name:

      router._providers["mock"] = MockLLMProvider(script)
    """

    provider_name = "mock"

    def __init__(self, script: LLMScript | list[LLMTurn] | None = None) -> None:
        super().__init__({})
        if isinstance(script, list):
            script = LLMScript(script)
        self._script: LLMScript = script or LLMScript([LLMTurn(content="Mock response")])
        self.call_count = 0
        self.last_messages: list[dict] = []
        self.last_system: str | None = None
        self.last_tools: list[dict] | None = None

    def reset(self) -> None:
        self._script.reset()
        self.call_count = 0

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        **kwargs,
    ) -> LLMResponse:
        self.call_count += 1
        self.last_messages = messages
        self.last_system = system
        self.last_tools = tools

        turn = self._script.next_turn()

        return LLMResponse(
            content=turn.content,
            tool_calls=turn.tool_calls,
            stop_reason=turn.stop_reason,
            model=turn.model,
            provider=self.provider_name,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
        )
