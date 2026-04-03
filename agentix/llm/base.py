"""
Base interface for all LLM provider adapters.

Every provider must implement `complete()` which takes a list of
messages (OpenAI-style dicts) and returns a `LLMResponse`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    role: str           # "system" | "user" | "assistant"
    content: str | list  # str or list[ContentBlock]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"           # end_turn | tool_use | max_tokens | stop
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    raw: Any = field(default=None, repr=False)


class BaseLLMProvider:
    """Abstract base — all adapters must implement complete()."""

    provider_name: str = "base"

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg

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
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider_name}>"
