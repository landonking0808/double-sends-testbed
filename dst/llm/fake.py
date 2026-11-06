"""A deterministic offline client for tests and scripted runs (no network, no quota)."""
from __future__ import annotations

import os
from typing import Callable, Optional, Union

from .base import Usage, check_messages
from .cache import CacheMiss, ResponseCache, request_key

Responder = Union[str, list, Callable[[list, dict], str]]


class FakeClient:
    """``complete`` returns scripted text: a fixed string, the next item of a list
    (cycled), or ``fn(messages, params)``. Calls are recorded in ``self.calls``.
    With ``cache_dir`` it uses the same cache (and ``replay_only``) as a real client."""

    provider = "fake"

    def __init__(self, responder: Responder = '{"tool":"read_inbox","args":{}}', *,
                 cache_dir: Optional[os.PathLike] = None, replay_only: bool = False):
        self.responder = responder
        self.calls: list = []
        self._i = 0
        self.replay_only = replay_only
        self.cache = None
        if cache_dir is not None:
            self.cache = ResponseCache(cache_dir)
        self.usage = Usage()

    def complete(self, messages: list, *, model: str = "fake", temperature: float = 0.0,
                 seed: int = 0, max_tokens: int = 512) -> str:
        check_messages(messages)
        params = {"model": model, "temperature": temperature, "seed": seed, "max_tokens": max_tokens}
        key = request_key(self.provider, model, messages, temperature, seed, max_tokens)
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                self.usage.cache_hits += 1
                return hit["text"]
            if self.replay_only:
                raise CacheMiss(f"replay_only: no cached response for fake:{model} key {key}")
        self.calls.append({"messages": [dict(m) for m in messages], **params})
        if callable(self.responder):
            text = self.responder(messages, params)
        elif isinstance(self.responder, list):
            text = self.responder[self._i % len(self.responder)]
            self._i += 1
        else:
            text = self.responder
        self.usage.calls += 1
        self.usage.completions += 1
        if self.cache is not None:
            self.cache.put(key, {"request": {"provider": self.provider, **params, "messages": messages},
                                 "text": text, "usage": {}})
        return text
