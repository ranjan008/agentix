"""Regression tests for LLMRouter's per-provider call timeout.

No provider adapter (anthropic_provider.py, openai_provider.py,
local_provider.py) ever sets an explicit client-level timeout, so every
call inherited that SDK's own default — 600s for both the openai and
anthropic python SDKs (confirmed against the installed packages'
DEFAULT_TIMEOUT). In a multi-provider fallback_chain, a single provider
that's merely slow (not erroring) could block the entire chain for minutes
before the next provider was ever tried. Found live investigating a graph
run that took 209s across 4 LLM calls.
"""
from __future__ import annotations

import asyncio

import pytest

from agentix.llm.base import BaseLLMProvider, LLMResponse
from agentix.llm.router import LLMRouter, _PROVIDER_REGISTRY


class _SlowProvider(BaseLLMProvider):
    """Sleeps longer than any sane test timeout before ever returning,
    simulating a provider that's stalled rather than erroring."""

    provider_name = "slow"

    async def complete(self, messages, model=None, tools=None, system=None,
                        max_tokens=4096, temperature=1.0, **kwargs) -> LLMResponse:
        await asyncio.sleep(5.0)
        return LLMResponse(
            content="too late", tool_calls=[], stop_reason="end_turn",
            model=model or "slow-model", provider=self.provider_name,
            input_tokens=1, output_tokens=1, raw=None,
        )


class _FastProvider(BaseLLMProvider):
    provider_name = "fast"

    async def complete(self, messages, model=None, tools=None, system=None,
                        max_tokens=4096, temperature=1.0, **kwargs) -> LLMResponse:
        return LLMResponse(
            content="quick reply", tool_calls=[], stop_reason="end_turn",
            model=model or "fast-model", provider=self.provider_name,
            input_tokens=1, output_tokens=1, raw=None,
        )


@pytest.fixture(autouse=True)
def _register_test_providers():
    _PROVIDER_REGISTRY["slow"] = _SlowProvider
    _PROVIDER_REGISTRY["fast"] = _FastProvider
    yield
    del _PROVIDER_REGISTRY["slow"]
    del _PROVIDER_REGISTRY["fast"]


@pytest.mark.asyncio
async def test_slow_provider_times_out_and_falls_through_to_next() -> None:
    """The actual regression: a provider that hangs (rather than erroring)
    must not block the whole chain — the router should give up on it after
    its configured timeout_sec and try the next provider, well within the
    slow provider's own 5s sleep."""
    cfg = {
        "llm": {
            "default_provider": "slow",
            "providers": {
                "slow": {"provider_type": "slow", "timeout_sec": 0.05},
                "fast": {"provider_type": "fast"},
            },
            "routing": {"fallback_chain": ["slow", "fast"]},
        }
    }
    router = LLMRouter(cfg)

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    resp = await router.complete(messages=[{"role": "user", "content": "hi"}], provider="slow")
    elapsed = loop.time() - t0

    assert resp.content == "quick reply"
    assert elapsed < 1.0  # would be >=5s without the timeout


@pytest.mark.asyncio
async def test_timeout_defaults_when_not_configured() -> None:
    """A provider with no timeout_sec set still gets SOME bound (the
    module default), not an unbounded wait — confirmed by using a default
    short enough for a fast test via a monkeypatched module constant."""
    import agentix.llm.router as router_mod

    original_default = router_mod.DEFAULT_PROVIDER_TIMEOUT_SEC
    router_mod.DEFAULT_PROVIDER_TIMEOUT_SEC = 0.05
    try:
        cfg = {
            "llm": {
                "default_provider": "slow",
                "providers": {
                    "slow": {"provider_type": "slow"},  # no timeout_sec set
                    "fast": {"provider_type": "fast"},
                },
                "routing": {"fallback_chain": ["slow", "fast"]},
            }
        }
        router = LLMRouter(cfg)
        resp = await router.complete(messages=[{"role": "user", "content": "hi"}], provider="slow")
        assert resp.content == "quick reply"
    finally:
        router_mod.DEFAULT_PROVIDER_TIMEOUT_SEC = original_default
