"""
Anthropic Claude provider adapter.

Wraps the existing agentix.agent_runtime.llm_client but conforms to BaseLLMProvider.
Supports claude-sonnet-4-6 and any other Claude model.
"""
from __future__ import annotations

import os
from typing import Any

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class AnthropicProvider(BaseLLMProvider):
    provider_name = "anthropic"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
        self._default_model = cfg.get("model", "claude-sonnet-4-6")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

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
        client = self._get_client()
        model = model or self._default_model

        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools
        if temperature != 1.0:
            params["temperature"] = temperature

        resp = await client.messages.create(**params)

        tool_calls = []
        text_parts = []
        # Build serialisable content blocks to preserve tool_use blocks
        # so the agentic loop can include them verbatim in the assistant message.
        raw_blocks = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                raw_blocks.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
                raw_blocks.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})

        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            model=resp.model,
            provider=self.provider_name,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            raw={"response": resp, "blocks": raw_blocks},
        )
