"""LLM client layer: free-tier providers only, every response cached, byte-identical replay.

    from dst.llm import get_client
    llm = get_client("gemini")                     # or "cloudflare", "fake"
    text = llm.complete([{"role": "user", "content": "hi"}],
                        model="gemma-4-26b-a4b-it", temperature=0.7, seed=1, max_tokens=256)

``get_client(provider, replay_only=True)`` never touches the network: a cache miss raises
``CacheMiss``. A call that would pass 90% of a free daily quota raises ``QuotaExhausted``
before anything is sent. See docs/MODELS.md for the models, their limits and the $0 evidence.
"""
from __future__ import annotations

from .base import BaseClient, LLMError, Usage
from .cache import CacheMiss, ResponseCache, request_key
from .fake import FakeClient
from .limits import Limits, QuotaExhausted
from .providers import CloudflareClient, GeminiClient

# The two models recommended in docs/MODELS.md.
MODEL_A = ("gemini", "gemma-4-26b-a4b-it")
MODEL_B = ("cloudflare", "@cf/ibm-granite/granite-4.0-h-micro")

PROVIDERS = {"gemini": GeminiClient, "cloudflare": CloudflareClient, "fake": FakeClient}


def get_client(provider: str, **kwargs):
    """Return a client with ``complete(messages, *, model, temperature, seed, max_tokens=512) -> str``.

    kwargs (real providers): cache_dir="runs/cache", quota_dir="runs/cache/_quota",
    usage_log="runs/usage/llm_calls.jsonl", replay_only=False, timeout=60.0, max_retries=6,
    http=<httpx.Client> (tests), limits={model: Limits} (override), api_key/api_token/account_id.
    """
    try:
        cls = PROVIDERS[provider]
    except KeyError:
        raise ValueError(f"unknown provider {provider!r}; one of {sorted(PROVIDERS)}") from None
    if provider == "fake":
        return cls(**kwargs)
    if kwargs.get("replay_only"):
        # replay needs no credential: give a placeholder so no secret is required offline
        if provider == "gemini":
            kwargs.setdefault("api_key", "replay-only")
        if provider == "cloudflare":
            kwargs.setdefault("api_token", "replay-only")
            kwargs.setdefault("account_id", "replay-only")
    return cls(**kwargs)


__all__ = ["get_client", "BaseClient", "GeminiClient", "CloudflareClient", "FakeClient", "CacheMiss",
           "QuotaExhausted", "LLMError", "Limits", "Usage", "ResponseCache", "request_key",
           "MODEL_A", "MODEL_B"]
