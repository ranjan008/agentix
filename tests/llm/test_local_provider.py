"""Regression test for LocalProvider's `extra_body` passthrough.

Found live wiring Z.ai's GLM-4.5-Flash into a fallback chain (see
test_router.py's docstring for the multi-instance backstory): GLM-4.5
defaults to emitting a chain-of-thought preamble into a separate
`reasoning_content` field before writing the real answer into `content`.
With a caller-specified low max_tokens, the reasoning pass alone can
exhaust the budget — the API call succeeds (no exception, finish_reason
"stop"), but `content` comes back empty, which LLMRouter has no way to
distinguish from "the model genuinely said nothing" and returns as if it
were a real answer.

Z.ai's own docs (https://docs.z.ai/guides/llm/glm-4.5) support disabling
this via `thinking: {type: disabled}` in the request body — a field
outside the openai SDK's typed create() signature, so it has to go through
extra_body, which LocalProvider never forwarded at all before this fix.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentix.llm.providers.local_provider import LocalProvider


def _fake_openai_response() -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "pong"
    resp.choices[0].message.tool_calls = None
    resp.choices[0].finish_reason = "stop"
    resp.model = "glm-4.5-flash"
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return resp


@pytest.mark.asyncio
async def test_extra_body_is_forwarded_to_the_request() -> None:
    provider = LocalProvider({
        "base_url": "https://api.z.ai/api/paas/v4",
        "model": "glm-4.5-flash",
        "api_key": "test",
        "extra_body": {"thinking": {"type": "disabled"}},
    })
    fake_create = AsyncMock(return_value=_fake_openai_response())
    provider._client = MagicMock()
    provider._client.chat.completions.create = fake_create

    await provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert fake_create.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_extra_body_defaults_to_absent_not_empty_dict() -> None:
    """Backward compat: no config sets extra_body today (local/ollama/
    lmstudio/vllm) — the request body must not gain a new key none of them
    asked for."""
    provider = LocalProvider({"base_url": "http://localhost:11434/v1", "model": "llama3.2"})
    fake_create = AsyncMock(return_value=_fake_openai_response())
    provider._client = MagicMock()
    provider._client.chat.completions.create = fake_create

    await provider.complete(messages=[{"role": "user", "content": "hi"}])

    assert "extra_body" not in fake_create.call_args.kwargs
