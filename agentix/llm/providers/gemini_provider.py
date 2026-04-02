"""
Google Gemini provider adapter.

Uses google-generativeai SDK.

Config keys / env:
  GOOGLE_API_KEY
  model: gemini-1.5-pro | gemini-1.5-flash | gemini-2.0-flash  (default)
"""
from __future__ import annotations

import os
from typing import Any

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class GeminiProvider(BaseLLMProvider):
    provider_name = "gemini"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._api_key = cfg.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
        self._default_model = cfg.get("model", "gemini-2.0-flash")
        self._genai = None

    def _get_genai(self):
        if self._genai is None:
            import google.generativeai as genai
            genai.configure(api_key=self._api_key)
            self._genai = genai
        return self._genai

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
        genai = self._get_genai()
        model_name = model or self._default_model

        generation_config = genai.GenerationConfig(
            max_output_tokens=max_tokens,
            temperature=temperature,
        )
        system_instruction = system or None

        m = genai.GenerativeModel(
            model_name=model_name,
            generation_config=generation_config,
            system_instruction=system_instruction,
            tools=_to_gemini_tools(tools) if tools else None,
        )

        history = _to_gemini_history(messages)
        last_user_msg = history.pop() if history and history[-1]["role"] == "user" else {"role": "user", "parts": [{"text": ""}]}

        chat = m.start_chat(history=history)
        resp = chat.send_message(last_user_msg["parts"])

        tool_calls = []
        text_parts = []
        for part in resp.parts:
            if hasattr(part, "text") and part.text:
                text_parts.append(part.text)
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.name,
                    name=fc.name,
                    input=dict(fc.args),
                ))

        usage = resp.usage_metadata
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            model=model_name,
            provider=self.provider_name,
            input_tokens=getattr(usage, "prompt_token_count", 0),
            output_tokens=getattr(usage, "candidates_token_count", 0),
            raw=resp,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_gemini_history(messages: list[dict]) -> list[dict]:
    role_map = {"user": "user", "assistant": "model", "system": "user"}
    history = []
    for m in messages:
        role = role_map.get(m.get("role", "user"), "user")
        content = m.get("content", "")
        if isinstance(content, list):
            parts = [{"text": b.get("text", "")} for b in content if b.get("type") == "text"]
        else:
            parts = [{"text": str(content)}]
        history.append({"role": role, "parts": parts})
    return history


def _to_gemini_tools(tools: list[dict]) -> list:
    """Convert Anthropic-style tool defs to Gemini FunctionDeclarations."""
    try:
        import google.generativeai.protos as protos
        declarations = []
        for t in tools:
            schema = t.get("input_schema", t.get("parameters", {}))
            declarations.append(protos.FunctionDeclaration(
                name=t["name"],
                description=t.get("description", ""),
                parameters=schema,
            ))
        return [protos.Tool(function_declarations=declarations)]
    except Exception:
        return []
