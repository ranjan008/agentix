"""
LLM Router — model selection, load balancing, fallback, and cost routing.

Configuration example (watchdog.yaml):

  llm:
    default_provider: anthropic
    providers:
      anthropic:
        api_key: ${ANTHROPIC_API_KEY}
        model: claude-sonnet-4-6
      openai:
        api_key: ${OPENAI_API_KEY}
        model: gpt-4o
      azure_openai:
        api_key: ${AZURE_OPENAI_API_KEY}
        azure_endpoint: ${AZURE_OPENAI_ENDPOINT}
        model: gpt-4o
      gemini:
        api_key: ${GOOGLE_API_KEY}
        model: gemini-2.0-flash
      bedrock:
        model: anthropic.claude-sonnet-4-6-20251001-v1:0
      local:                                     # Ollama / LM Studio / vLLM
        base_url: http://localhost:11434/v1
        model: llama3.2

    routing:
      # Route by agent tag
      rules:
        - match_tag: fast
          provider: gemini
        - match_tag: cheap
          provider: openai
          model: gpt-4o-mini
        - match_tag: sensitive          # keep data in-region
          provider: azure_openai
      # Fallback chain when a provider errors
      fallback_chain:
        - anthropic
        - openai
        - gemini
"""
from __future__ import annotations

import logging
import time

from agentix.llm.base import BaseLLMProvider, LLMResponse

log = logging.getLogger(__name__)

_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {}


def _register(name: str):
    def decorator(cls: type[BaseLLMProvider]):
        _PROVIDER_REGISTRY[name] = cls
        return cls
    return decorator


def _load_providers() -> None:
    """Lazy import so unused SDKs don't fail at startup."""
    from agentix.llm.providers.anthropic_provider import AnthropicProvider
    from agentix.llm.providers.openai_provider import OpenAIProvider, AzureOpenAIProvider
    from agentix.llm.providers.gemini_provider import GeminiProvider
    from agentix.llm.providers.bedrock_provider import BedrockProvider
    from agentix.llm.providers.local_provider import LocalProvider

    _PROVIDER_REGISTRY.update({
        "anthropic": AnthropicProvider,
        "openai": OpenAIProvider,
        "azure_openai": AzureOpenAIProvider,
        "gemini": GeminiProvider,
        "bedrock": BedrockProvider,
        "local": LocalProvider,
        # Convenience aliases
        "ollama": LocalProvider,
        "lmstudio": LocalProvider,
        "vllm": LocalProvider,
    })


class LLMRouter:
    """
    Central LLM dispatcher.

    complete() selects a provider based on routing rules, calls it,
    and falls back to the next provider in the chain on error.
    """

    def __init__(self, cfg: dict) -> None:
        _load_providers()
        llm_cfg = cfg.get("llm", {})
        self._default_provider_name: str = llm_cfg.get("default_provider", "anthropic")
        self._routing_rules: list[dict] = llm_cfg.get("routing", {}).get("rules", [])
        self._fallback_chain: list[str] = llm_cfg.get("routing", {}).get("fallback_chain", [])

        # Instantiate configured providers
        self._providers: dict[str, BaseLLMProvider] = {}
        for name, pcfg in llm_cfg.get("providers", {}).items():
            cls = _PROVIDER_REGISTRY.get(name)
            if cls is None:
                log.warning("LLMRouter: unknown provider '%s' — skipping", name)
                continue
            try:
                self._providers[name] = cls(pcfg)
                log.info("LLMRouter: registered provider '%s'", name)
            except Exception as exc:
                log.error("LLMRouter: failed to init provider '%s': %s", name, exc)

        # Ensure default provider is available
        if self._default_provider_name not in self._providers:
            log.info("LLMRouter: default provider '%s' not configured — instantiating with env defaults", self._default_provider_name)
            cls = _PROVIDER_REGISTRY.get(self._default_provider_name)
            if cls:
                self._providers[self._default_provider_name] = cls({})

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        tools: list[dict] | None = None,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 1.0,
        tags: list[str] | None = None,
        provider: str | None = None,
        **kwargs,
    ) -> LLMResponse:
        """
        Route and call the appropriate LLM provider.

        Args:
            messages:    Conversation history (Anthropic-style dicts)
            model:       Optional model override
            tools:       Tool definitions
            system:      System prompt
            max_tokens:  Max output tokens
            temperature: Sampling temperature
            tags:        Agent tags used for routing rule matching
            provider:    Force a specific provider by name
            **kwargs:    Passed through to the provider
        """
        provider_name, model = self._select(provider, model, tags)
        chain = self._build_fallback_chain(provider_name)

        last_exc: Exception | None = None
        for pname in chain:
            p = self._providers.get(pname)
            if p is None:
                log.warning("LLMRouter: provider '%s' not available, skipping", pname)
                continue
            try:
                t0 = time.monotonic()
                resp = await p.complete(
                    messages=messages,
                    model=model,
                    tools=tools,
                    system=system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                elapsed = time.monotonic() - t0
                log.debug("LLMRouter: %s %.2fs in=%d out=%d", pname, elapsed, resp.input_tokens, resp.output_tokens)
                return resp
            except Exception as exc:
                log.warning("LLMRouter: provider '%s' failed: %s — trying next", pname, exc)
                last_exc = exc

        raise RuntimeError(f"All LLM providers failed. Last error: {last_exc}") from last_exc

    def get_provider(self, name: str) -> BaseLLMProvider | None:
        return self._providers.get(name)

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    # ------------------------------------------------------------------
    # Routing logic
    # ------------------------------------------------------------------

    def _select(self, forced_provider: str | None, forced_model: str | None, tags: list[str] | None) -> tuple[str, str | None]:
        """Return (provider_name, model_override)."""
        if forced_provider:
            return forced_provider, forced_model

        if tags:
            for rule in self._routing_rules:
                if rule.get("match_tag") in tags:
                    return rule["provider"], rule.get("model", forced_model)

        return self._default_provider_name, forced_model

    def _build_fallback_chain(self, primary: str) -> list[str]:
        chain = [primary]
        for fb in self._fallback_chain:
            if fb not in chain:
                chain.append(fb)
        return chain


def build_router(cfg: dict) -> LLMRouter:
    """Factory — builds an LLMRouter from the watchdog config dict."""
    return LLMRouter(cfg)
