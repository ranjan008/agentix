"""
AWS Bedrock provider adapter.

Supports Claude (via Anthropic Messages API on Bedrock) and other Bedrock models.

Config keys / env:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
  BEDROCK_REGION  — override region for Bedrock specifically
  model: anthropic.claude-sonnet-4-6-20251001-v1:0  (default)
"""
from __future__ import annotations

import json
import os

from agentix.llm.base import BaseLLMProvider, LLMResponse, ToolCall


class BedrockProvider(BaseLLMProvider):
    provider_name = "bedrock"

    # Bedrock model IDs that use the Anthropic Messages API
    _ANTHROPIC_MODELS = frozenset({
        "anthropic.claude",
    })

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self._region = cfg.get("bedrock_region") or os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self._default_model = cfg.get("model", "anthropic.claude-sonnet-4-6-20251001-v1:0")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _is_anthropic(self, model: str) -> bool:
        return model.startswith("anthropic.")

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
        model = model or self._default_model
        return await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._complete_sync(messages, model, tools, system, max_tokens, temperature),
        )

    def _complete_sync(self, messages, model, tools, system, max_tokens, temperature) -> LLMResponse:
        client = self._get_client()

        if self._is_anthropic(model):
            return self._anthropic_invoke(client, messages, model, tools, system, max_tokens, temperature)
        else:
            return self._converse_invoke(client, messages, model, tools, system, max_tokens, temperature)

    # ------------------------------------------------------------------
    # Anthropic Claude on Bedrock (Messages API)
    # ------------------------------------------------------------------

    def _anthropic_invoke(self, client, messages, model, tools, system, max_tokens, temperature) -> LLMResponse:
        body: dict = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        if temperature != 1.0:
            body["temperature"] = temperature

        resp = client.invoke_model(
            modelId=model,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        resp_body = json.loads(resp["body"].read())

        tool_calls = []
        text_parts = []
        for block in resp_body.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block["id"],
                    name=block["name"],
                    input=block.get("input", {}),
                ))

        usage = resp_body.get("usage", {})
        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=resp_body.get("stop_reason", "end_turn"),
            model=model,
            provider=self.provider_name,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            raw=resp_body,
        )

    # ------------------------------------------------------------------
    # Generic Converse API (Titan, Llama, Mistral, etc.)
    # ------------------------------------------------------------------

    def _converse_invoke(self, client, messages, model, tools, system, max_tokens, temperature) -> LLMResponse:
        converse_msgs = [
            {"role": m["role"], "content": [{"text": m["content"] if isinstance(m["content"], str) else str(m["content"])}]}
            for m in messages
        ]
        params: dict = {
            "modelId": model,
            "messages": converse_msgs,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            params["system"] = [{"text": system}]

        resp = client.converse(**params)
        msg = resp.get("output", {}).get("message", {})
        content_list = msg.get("content", [])
        text = " ".join(c.get("text", "") for c in content_list if "text" in c)
        usage = resp.get("usage", {})
        return LLMResponse(
            content=text,
            tool_calls=[],
            stop_reason=resp.get("stopReason", "end_turn"),
            model=model,
            provider=self.provider_name,
            input_tokens=usage.get("inputTokens", 0),
            output_tokens=usage.get("outputTokens", 0),
            raw=resp,
        )
