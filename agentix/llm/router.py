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
      # Any name works as a provider key — `provider_type` (defaulting to
      # the key itself) picks the class from _PROVIDER_REGISTRY, so
      # several distinct OpenAI-compatible endpoints can coexist, each
      # under its own name, all sharing the "local" class:
      qwen:
        provider_type: local
        base_url: https://dashscope-intl.aliyuncs.com/compatible-mode/v1
        api_key: ${QWEN_API_KEY}
        model: qwen-turbo
      zai:
        provider_type: local
        base_url: https://api.z.ai/api/paas/v4
        api_key: ${ZAI_API_KEY}
        model: glm-4.5-flash
      # timeout_sec (any provider, default 45s — see
      # DEFAULT_PROVIDER_TIMEOUT_SEC below): bounds how long ONE attempt in
      # a fallback_chain can take before the router gives up on it and
      # tries the next provider. Without this, a merely-slow (not erroring)
      # provider can stall the whole chain for minutes, since none of the
      # provider SDKs set their own client-level timeout.
      slow_provider_example:
        provider_type: local
        base_url: https://example.com/v1
        timeout_sec: 20

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

import asyncio
import logging
import time

from agentix.llm.base import BaseLLMProvider, LLMResponse

log = logging.getLogger(__name__)

_PROVIDER_REGISTRY: dict[str, type[BaseLLMProvider]] = {}

# No provider adapter (anthropic_provider.py, openai_provider.py,
# local_provider.py) ever sets an explicit client-level timeout — each one
# just does `AsyncOpenAI(api_key=...)` / `AsyncAnthropic(api_key=...)` with
# no `timeout=`, so every call inherits that SDK's own default: 600s read
# timeout for both the openai and anthropic python SDKs (confirmed against
# the installed packages' own DEFAULT_TIMEOUT), and gemini_provider.py sets
# none either. In a multi-provider fallback_chain, a single provider that's
# merely slow (not erroring) can block the ENTIRE chain for minutes before
# the next provider is ever tried — found live investigating a graph run
# that took 209s across 4 LLM calls (a plausible full account: several
# providers each burning a large chunk of that time on a slow-but-not-yet-
# failed attempt before falling through, rather than the successful calls
# alone accounting for it). Bounding each attempt is a router-level
# concern, not something each provider adapter should reimplement
# separately — this wraps every call uniformly regardless of provider.
DEFAULT_PROVIDER_TIMEOUT_SEC = 45.0


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

        # Instantiate configured providers. The dict key is the provider's
        # *name* — what routing rules, fallback_chain, and complete()'s own
        # `provider=` argument all refer to it as. `provider_type` (falling
        # back to the name itself) is what selects the actual CLASS from
        # _PROVIDER_REGISTRY — this is what lets multiple distinct
        # instances of the same class (e.g. several OpenAI-compatible
        # endpoints) coexist under their own names, exactly as
        # local_provider.py's own docstring documents ("Multiple local
        # instances: vllm: provider_type: local, base_url: ..."). That
        # docstring described this class-vs-name split as already
        # supported; this loop previously only ever did
        # `_PROVIDER_REGISTRY.get(name)`, so any non-canonical key (that
        # exact "vllm" example included, since "vllm" already means
        # something else in the registry) silently logged "unknown
        # provider" and was never instantiated at all — found live trying
        # to add a second and third OpenAI-compatible provider to a
        # fallback chain that had already used all of local/ollama/
        # lmstudio/vllm, the registry's only four pre-registered aliases.
        self._providers: dict[str, BaseLLMProvider] = {}
        # Per-provider call timeout (seconds) — `timeout_sec` in that
        # provider's own config, defaulting to DEFAULT_PROVIDER_TIMEOUT_SEC
        # when unset (every existing watchdog.yaml today). See that
        # constant's comment for why this exists at all.
        self._provider_timeouts: dict[str, float] = {}
        for name, pcfg in llm_cfg.get("providers", {}).items():
            provider_type = pcfg.get("provider_type", name)
            cls = _PROVIDER_REGISTRY.get(provider_type)
            if cls is None:
                log.warning("LLMRouter: unknown provider '%s' (provider_type=%s) — skipping", name, provider_type)
                continue
            try:
                self._providers[name] = cls(pcfg)
                self._provider_timeouts[name] = float(pcfg.get("timeout_sec", DEFAULT_PROVIDER_TIMEOUT_SEC))
                log.info("LLMRouter: registered provider '%s' (type=%s)", name, provider_type)
            except Exception as exc:
                log.error("LLMRouter: failed to init provider '%s': %s", name, exc)

        # Ensure default provider is available
        if self._default_provider_name not in self._providers:
            log.info("LLMRouter: default provider '%s' not configured — instantiating with env defaults", self._default_provider_name)
            cls = _PROVIDER_REGISTRY.get(self._default_provider_name)
            if cls:
                self._providers[self._default_provider_name] = cls({})
                self._provider_timeouts[self._default_provider_name] = DEFAULT_PROVIDER_TIMEOUT_SEC

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
        for i, pname in enumerate(chain):
            p = self._providers.get(pname)
            if p is None:
                log.warning("LLMRouter: provider '%s' not available, skipping", pname)
                continue
            # Only pass the caller-specified model to the primary provider.
            # Fallback providers use their own configured default model.
            model_for_call = model if i == 0 else None
            # getattr, not self._provider_timeouts directly: agentix's own
            # testing.harness builds an LLMRouter via __new__() (bypassing
            # __init__ entirely) and hand-sets only the attributes it
            # needs — a real, established pattern, not a bug — so this
            # attribute can legitimately not exist on every instance.
            timeout = getattr(self, "_provider_timeouts", {}).get(pname, DEFAULT_PROVIDER_TIMEOUT_SEC)
            try:
                t0 = time.monotonic()
                resp = await asyncio.wait_for(
                    p.complete(
                        messages=messages,
                        model=model_for_call,
                        tools=tools,
                        system=system,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        **kwargs,
                    ),
                    timeout=timeout,
                )
                elapsed = time.monotonic() - t0
                log.debug("LLMRouter: %s %.2fs in=%d out=%d", pname, elapsed, resp.input_tokens, resp.output_tokens)
                return resp
            except TimeoutError:
                # Note for providers backed by a thread pool rather than a
                # native async client (gemini_provider.py uses
                # run_in_executor): wait_for only stops AWAITING the
                # result — the underlying thread keeps running to
                # completion in the background and its result is simply
                # discarded. That's fine here (no resource leak beyond one
                # extra outstanding thread, no result to act on), and it's
                # still what makes the router itself move on within
                # `timeout` seconds rather than blocking the whole chain.
                elapsed = time.monotonic() - t0
                log.warning("LLMRouter: provider '%s' timed out after %.0fs (limit=%.0fs) — trying next", pname, elapsed, timeout)
                last_exc = TimeoutError(f"provider '{pname}' timed out after {timeout:.0f}s")
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
