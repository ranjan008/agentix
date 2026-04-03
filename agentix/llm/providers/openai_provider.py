"""
OpenAI / Azure OpenAI provider adapter.

Set in config / env:
  OPENAI_API_KEY        — for openai.com
  AZURE_OPENAI_API_KEY  — for Azure OpenAI
  AZURE_OPENAI_ENDPOINT — https://<resource>.openai.azure.com
  AZURE_OPENAI_API_VERSION — e.g. 2024-02-01

Config keys:
  provider: openai | azure_openai
  api_key, model, azure_endpoint, azure_api_version, azure_deployment
"""
from __future__ import annotations

import os
from typing import Any

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class OpenAIProvider(BaseLLMProvider):
    provider_name = "openai"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._api_key = cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
        self._default_model = cfg.get("model", "gpt-4o")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._api_key)
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

        # Prepend system message if provided
        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        params: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            # Convert Anthropic-style tool defs to OpenAI function format
            params["tools"] = [_to_openai_tool(t) for t in tools]
            params["tool_choice"] = "auto"

        resp = await client.chat.completions.create(**params)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            import json
            for tc in msg.tool_calls:
                try:
                    inp = json.loads(tc.function.arguments)
                except Exception:
                    inp = {"_raw": tc.function.arguments}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))

        usage = resp.usage or type("U", (), {"prompt_tokens": 0, "completion_tokens": 0})()
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=_map_finish(choice.finish_reason),
            model=resp.model,
            provider=self.provider_name,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            raw=resp,
        )


class AzureOpenAIProvider(OpenAIProvider):
    provider_name = "azure_openai"

    def _get_client(self):
        if self._client is None:
            from openai import AsyncAzureOpenAI
            endpoint = self.cfg.get("azure_endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
            api_version = self.cfg.get("azure_api_version") or os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
            api_key = self.cfg.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY", "")
            self._client = AsyncAzureOpenAI(
                api_key=api_key,
                azure_endpoint=endpoint,
                api_version=api_version,
            )
        return self._client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_openai_tool(tool: dict) -> dict:
    """Convert Anthropic-style tool def to OpenAI function format."""
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", tool.get("parameters", {})),
        },
    }


def _map_finish(reason: str | None) -> str:
    mapping = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "stop",
    }
    return mapping.get(reason or "", "end_turn")
