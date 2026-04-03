"""
LLM Client — thin wrapper around the Anthropic SDK.
Supports multi-turn conversations and tool use (function calling).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import anthropic

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(
        self,
        provider: str = "anthropic",
        model_id: str = "claude-sonnet-4-6",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        api_key: str = "",
    ) -> None:
        if provider != "anthropic":
            raise ValueError(f"Phase 1 only supports 'anthropic' provider, got: {provider}")

        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        )

    def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> anthropic.types.Message:
        """
        Single call to the Anthropic Messages API.
        Returns the raw Message object (callers inspect .content and .stop_reason).
        """
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        logger.debug("LLM call: model=%s messages=%d tools=%d", self.model_id, len(messages), len(tools or []))
        response = self._client.messages.create(**kwargs)
        logger.debug("LLM response: stop_reason=%s", response.stop_reason)
        return response

    @classmethod
    def from_spec(cls, spec: dict) -> "LLMClient":
        model_cfg = spec["spec"]["model"]
        return cls(
            provider=model_cfg.get("provider", "anthropic"),
            model_id=model_cfg.get("model_id", "claude-sonnet-4-6"),
            temperature=model_cfg.get("temperature", 0.3),
            max_tokens=model_cfg.get("max_tokens", 4096),
        )
