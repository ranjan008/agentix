"""
Local / self-hosted LLM provider adapter.

Supports any OpenAI-compatible server running locally or on-premises:

  - Ollama          http://localhost:11434/v1   (default)
  - LM Studio       http://localhost:1234/v1
  - vLLM            http://localhost:8000/v1
  - llama.cpp       http://localhost:8080/v1
  - LocalAI         http://localhost:8080/v1
  - Anything else   set base_url explicitly

Config example (watchdog.yaml):

  llm:
    default_provider: local
    providers:
      local:
        base_url: http://localhost:11434/v1   # Ollama default
        model: llama3.2                        # any model pulled in Ollama
        api_key: ollama                        # placeholder — not validated locally

      # Multiple local instances:
      vllm:
        provider_type: local
        base_url: http://gpu-server:8000/v1
        model: mistral-7b-instruct
        api_key: ignored

Env vars:
  LOCAL_LLM_BASE_URL   — override base_url
  LOCAL_LLM_MODEL      — override model
  LOCAL_LLM_API_KEY    — override api_key (default: "ollama")

Tool use:
  Most quantised models support tool calling. If your model does not,
  set supports_tools: false in config and Agentix will fall back to
  prompt-based tool extraction.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall

log = logging.getLogger(__name__)


class LocalProvider(BaseLLMProvider):
    """
    OpenAI-compatible adapter for locally deployed models.

    Works with Ollama, LM Studio, vLLM, llama.cpp, LocalAI, and any
    server that implements the /v1/chat/completions endpoint.
    """

    provider_name = "local"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._base_url: str = (
            cfg.get("base_url")
            or os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
        )
        self._default_model: str = (
            cfg.get("model")
            or os.environ.get("LOCAL_LLM_MODEL", "llama3.2")
        )
        # Local servers don't validate keys but the openai SDK requires one
        self._api_key: str = (
            cfg.get("api_key")
            or os.environ.get("LOCAL_LLM_API_KEY", "ollama")
        )
        self._supports_tools: bool = cfg.get("supports_tools", True)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )
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

        full_messages: list[dict] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        params: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # Only pass tool definitions if the model supports them
        if tools and self._supports_tools:
            params["tools"] = [_to_openai_tool(t) for t in tools]
            params["tool_choice"] = "auto"

        log.debug("LocalProvider → %s  model=%s", self._base_url, model)
        resp = await client.chat.completions.create(**params)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            import json
            for tc in msg.tool_calls:
                try:
                    inp = json.loads(tc.function.arguments)
                except Exception:
                    inp = {"_raw": tc.function.arguments}
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))

        usage = resp.usage
        return LLMResponse(
            content=msg.content or "",
            tool_calls=tool_calls,
            stop_reason=_map_finish(choice.finish_reason),
            model=resp.model,
            provider=self.provider_name,
            input_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            output_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            raw=resp,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_openai_tool(tool: dict) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", tool.get("parameters", {})),
        },
    }


def _map_finish(reason: str | None) -> str:
    return {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "stop",
    }.get(reason or "", "end_turn")
