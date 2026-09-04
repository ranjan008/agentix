"""
Regression tests for LLMRouter's provider instantiation — specifically
`provider_type`, which local_provider.py's own docstring has long
documented ("Multiple local instances: vllm: provider_type: local, ...")
but LLMRouter never actually implemented: it only ever looked a provider
up in _PROVIDER_REGISTRY by its config KEY (the name), with no way to
give a second or third instance of the same underlying class its own
name. A YAML entry shaped exactly like the documented example silently
logged "unknown provider" and was never instantiated at all.

Found live trying to add a second and third OpenAI-compatible provider
(Qwen, Z.ai) to a fallback chain that had already used all four of the
registry's pre-registered generic-class aliases (local/ollama/lmstudio/
vllm) for other services (Groq/Cerebras/Mistral) — there was no way to
add a fifth without this fix, despite the docstring implying otherwise.
"""
from __future__ import annotations

import pytest

from agentix.llm.base import BaseLLMProvider, LLMResponse
from agentix.llm.router import LLMRouter, _PROVIDER_REGISTRY


class _FakeProvider(BaseLLMProvider):
    """Records the config it was built with instead of calling a real API."""

    provider_name = "fake"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.calls: list[dict] = []

    async def complete(self, messages, model=None, tools=None, system=None,
                        max_tokens=4096, temperature=1.0, **kwargs) -> LLMResponse:
        self.calls.append({"model": model, "messages": messages})
        return LLMResponse(
            content=f"reply from {self.cfg.get('base_url', 'fake')}",
            tool_calls=[], stop_reason="end_turn", model=model or "fake-model",
            provider=self.provider_name, input_tokens=1, output_tokens=1, raw=None,
        )


@pytest.fixture(autouse=True)
def _register_fake_provider():
    """Registered under "fake" only — never one of the real aliases, so
    these tests can't accidentally pass because "qwen"/"zai" happen to
    collide with something already in the registry."""
    _PROVIDER_REGISTRY["fake"] = _FakeProvider
    yield
    del _PROVIDER_REGISTRY["fake"]


def test_provider_type_lets_two_named_instances_share_one_class() -> None:
    """The actual regression: two arbitrarily-named provider entries,
    "qwen" and "zai", both with provider_type: fake — both must be
    instantiated as real, independently-callable _FakeProvider instances,
    not silently skipped as "unknown provider"."""
    cfg = {
        "llm": {
            "default_provider": "qwen",
            "providers": {
                "qwen": {"provider_type": "fake", "base_url": "https://dashscope.example/v1"},
                "zai": {"provider_type": "fake", "base_url": "https://api.z.ai/v1"},
            },
        }
    }
    router = LLMRouter(cfg)

    assert "qwen" in router.available_providers
    assert "zai" in router.available_providers
    assert router.get_provider("qwen").cfg["base_url"] == "https://dashscope.example/v1"
    assert router.get_provider("zai").cfg["base_url"] == "https://api.z.ai/v1"


def test_provider_type_defaults_to_the_config_key_name() -> None:
    """Backward compatibility: an entry with no provider_type at all must
    behave exactly as before — the key IS the type, same as every
    existing watchdog.yaml in production today."""
    cfg = {
        "llm": {
            "default_provider": "fake",
            "providers": {"fake": {"base_url": "https://example/v1"}},
        }
    }
    router = LLMRouter(cfg)
    assert "fake" in router.available_providers


def test_unknown_provider_type_is_skipped_not_crashed() -> None:
    cfg = {
        "llm": {
            "default_provider": "fake",
            "providers": {
                "fake": {"base_url": "https://example/v1"},
                "bogus": {"provider_type": "does-not-exist"},
            },
        }
    }
    router = LLMRouter(cfg)
    assert "fake" in router.available_providers
    assert "bogus" not in router.available_providers


@pytest.mark.asyncio
async def test_two_named_instances_are_independently_callable() -> None:
    """Not just registered — genuinely two separate, working providers,
    each callable by its own name via complete(provider=...)."""
    cfg = {
        "llm": {
            "default_provider": "qwen",
            "providers": {
                "qwen": {"provider_type": "fake", "base_url": "https://dashscope.example/v1"},
                "zai": {"provider_type": "fake", "base_url": "https://api.z.ai/v1"},
            },
        }
    }
    router = LLMRouter(cfg)

    r1 = await router.complete(messages=[{"role": "user", "content": "hi"}], provider="qwen")
    r2 = await router.complete(messages=[{"role": "user", "content": "hi"}], provider="zai")

    assert "dashscope.example" in r1.content
    assert "api.z.ai" in r2.content


def test_fallback_chain_can_include_multiple_provider_type_instances() -> None:
    """The whole point: a fallback_chain naming several same-class
    instances by their own distinct names — this is what a free-tier pool
    wiring in Qwen AND Z.ai (not just one or the other) actually needs."""
    cfg = {
        "llm": {
            "default_provider": "qwen",
            "providers": {
                "qwen": {"provider_type": "fake", "base_url": "https://dashscope.example/v1"},
                "zai": {"provider_type": "fake", "base_url": "https://api.z.ai/v1"},
            },
            "routing": {"fallback_chain": ["qwen", "zai"]},
        }
    }
    router = LLMRouter(cfg)
    chain = router._build_fallback_chain("qwen")
    assert chain == ["qwen", "zai"]
