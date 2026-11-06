"""Provider adapters: build one HTTP request and parse one response.

* ``gemini``     - Google Gemini API (generativelanguage.googleapis.com) REST, Gemma 4 models.
* ``cloudflare`` - Cloudflare Workers AI REST (/accounts/{id}/ai/run/{model}).

Credentials are read from the environment (``GEMINI_API_KEY``; ``CLOUDFLARE_API_TOKEN`` and
``CLOUDFLARE_ACCOUNT_ID``) or from files named by ``GEMINI_API_KEY_FILE`` etc. They are only
placed in request headers, never logged, cached or returned.
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

from .base import BaseClient, LLMError, Result


def _secret(name: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit.strip()
    if os.environ.get(name):
        return os.environ[name].strip()
    path = os.environ.get(name + "_FILE")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    raise LLMError(f"missing credential: set {name} or {name}_FILE")


class GeminiClient(BaseClient):
    provider = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    DEFAULT_THINKING = "minimal"

    def __init__(self, api_key: Optional[str] = None, thinking_level: Optional[str] = DEFAULT_THINKING, **kw):
        """thinking_level: Gemma 4 thinks by default and its thoughts count against
        maxOutputTokens (a 64-token budget can end with no answer). "minimal" (default)
        turns that down; None sends no thinkingConfig. A non-default value is part of the
        cache key."""
        super().__init__(**kw)
        self._key = _secret("GEMINI_API_KEY", api_key)
        self.thinking_level = thinking_level

    @property
    def cache_tag(self) -> str:
        if self.thinking_level == self.DEFAULT_THINKING:
            return self.provider
        return f"{self.provider}+thinking={self.thinking_level}"

    @staticmethod
    def to_contents(messages: list) -> list:
        """Gemini roles are user/model. System text is folded into the first user turn
        (Gemma on the Gemini API does not take a separate system instruction), and
        consecutive same-role turns are merged so roles alternate."""
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = []
        for m in messages:
            if m["role"] == "system":
                continue
            role = "model" if m["role"] == "assistant" else "user"
            text = m["content"]
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"][0]["text"] += "\n\n" + text
            else:
                contents.append({"role": role, "parts": [{"text": text}]})
        if system:
            if contents and contents[0]["role"] == "user":
                contents[0]["parts"][0]["text"] = system + "\n\n" + contents[0]["parts"][0]["text"]
            else:
                contents.insert(0, {"role": "user", "parts": [{"text": system}]})
        return contents

    def _build(self, messages, model, temperature, seed, max_tokens):
        body = {"contents": self.to_contents(messages),
                "generationConfig": {"temperature": temperature, "seed": int(seed),
                                     "maxOutputTokens": int(max_tokens)}}
        if self.thinking_level:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        return self.http.build_request("POST", f"{self.BASE}/models/{model}:generateContent",
                                       json=body, headers={"x-goog-api-key": self._key})

    def _parse(self, resp: httpx.Response) -> Result:
        d = resp.json()
        cands = d.get("candidates") or []
        if not cands:
            raise LLMError(f"gemini: no candidates (promptFeedback={d.get('promptFeedback')})", 200)
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = d.get("usageMetadata") or {}
        out = None
        if "candidatesTokenCount" in u or "thoughtsTokenCount" in u:
            out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
        return Result(text=text, input_tokens=u.get("promptTokenCount"), output_tokens=out, raw_usage=u,
                      finish_reason=cands[0].get("finishReason"))


class CloudflareClient(BaseClient):
    provider = "cloudflare"
    BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: Optional[str] = None, account_id: Optional[str] = None, **kw):
        super().__init__(**kw)
        self._token = _secret("CLOUDFLARE_API_TOKEN", api_token)
        self._account = _secret("CLOUDFLARE_ACCOUNT_ID", account_id)

    def _build(self, messages, model, temperature, seed, max_tokens):
        # Workers AI requires seed >= 1, so testbed seed s is sent as s + 1 (the cache key keeps s).
        body = {"messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                "temperature": temperature, "seed": int(seed) + 1, "max_tokens": int(max_tokens)}
        return self.http.build_request("POST", f"{self.BASE}/accounts/{self._account}/ai/run/{model}",
                                       json=body, headers={"Authorization": f"Bearer {self._token}"})

    def _parse(self, resp: httpx.Response) -> Result:
        d = resp.json()
        if not d.get("success", True):
            raise LLMError(f"cloudflare: {d.get('errors')}", 200)
        r = d.get("result") or {}
        text = r.get("response")
        if text is None and r.get("choices"):            # OpenAI-shaped models
            text = (r["choices"][0].get("message") or {}).get("content", "")
        if not isinstance(text, str):                      # some models return parsed JSON
            import json
            text = json.dumps(text, sort_keys=True)
        u = r.get("usage") or {}
        fin = None
        if r.get("choices"):
            fin = r["choices"][0].get("finish_reason")
        return Result(text=text, input_tokens=u.get("prompt_tokens"),
                      output_tokens=u.get("completion_tokens"), raw_usage=u, finish_reason=fin)
