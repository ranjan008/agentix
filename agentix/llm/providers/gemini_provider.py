"""
Google Gemini provider adapter.

Uses the google-genai SDK (google.genai).

Config keys:
  api_key_env: env-var name holding the key (default: GOOGLE_API_KEY)
  api_key:     literal key (takes precedence)
  model:       gemini-2.5-flash-lite | gemini-2.0-flash  (default)
"""
from __future__ import annotations

import logging
import os

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall

log = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    provider_name = "gemini"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        key_env = cfg.get("api_key_env", "GOOGLE_API_KEY")
        self._api_key = cfg.get("api_key") or os.environ.get(key_env, "")
        self._default_model = cfg.get("model", "gemini-2.0-flash")

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
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._complete_sync(messages, model, tools, system, max_tokens, temperature),
        )

    def _complete_sync(self, messages, model, tools, system, max_tokens, temperature) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        model_name = model or self._default_model

        contents = _to_genai_contents(messages)

        config_kwargs: dict = {
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            config_kwargs["system_instruction"] = system
        if tools:
            config_kwargs["tools"] = [_to_genai_tools(tools)]

        config = types.GenerateContentConfig(**config_kwargs)

        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

        log.debug("Gemini raw response: candidates=%s", response.candidates)

        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        for candidate in response.candidates or []:
            finish = getattr(candidate, "finish_reason", None)
            log.debug("Gemini candidate finish_reason=%s", finish)
            content = getattr(candidate, "content", None)
            for part in (content.parts if content and content.parts else []):
                t = getattr(part, "text", None)
                if t:
                    text_parts.append(t)
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    tool_calls.append(ToolCall(
                        id=fc.name,
                        name=fc.name,
                        input=dict(fc.args) if fc.args else {},
                    ))

        # Fallback: use SDK convenience property if no parts were parsed
        if not text_parts and not tool_calls:
            try:
                t = response.text
                if t:
                    text_parts.append(t)
            except Exception:
                pass

        if not text_parts and not tool_calls:
            log.warning("Gemini returned no text and no tool calls. Full response: %s", response)

        usage = response.usage_metadata
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            model=model_name,
            provider=self.provider_name,
            input_tokens=getattr(usage, "prompt_token_count", 0) if usage else 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) if usage else 0,
            raw=response,
        )


def _to_genai_contents(messages: list[dict]) -> list:
    """Convert Anthropic-style messages to google-genai Content objects."""
    from google.genai import types

    role_map = {"user": "user", "assistant": "model", "system": "user"}
    contents = []
    for m in messages:
        role = role_map.get(m.get("role", "user"), "user")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(types.Part(text=block.get("text", "")))
                elif block.get("type") == "tool_result":
                    parts.append(types.Part(text=str(block.get("content", ""))))
            if not parts:
                parts = [types.Part(text="")]
        else:
            parts = [types.Part(text=str(content))]
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _to_genai_tools(tools: list[dict]):
    """Convert Anthropic-style tool defs to a google-genai Tool object."""
    from google.genai import types

    declarations = []
    for t in tools:
        schema = t.get("input_schema", t.get("parameters", {}))
        declarations.append(types.FunctionDeclaration(
            name=t["name"],
            description=t.get("description", ""),
            parameters=schema,
        ))
    return types.Tool(function_declarations=declarations)
