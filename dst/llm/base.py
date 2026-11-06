"""The provider-independent client: cache, pacing, daily quota, retries, usage log."""
from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import httpx

from .cache import CacheMiss, ResponseCache, request_key
from .limits import Governor, Limits, QuotaExhausted, estimate_tokens, limits_for

CT = ZoneInfo("America/Chicago")
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """A non-retryable provider error, or retries exhausted."""

    def __init__(self, msg: str, status: Optional[int] = None):
        super().__init__(msg)
        self.status = status


@dataclass
class Result:
    text: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    raw_usage: dict = field(default_factory=dict)
    finish_reason: Optional[str] = None


@dataclass
class Usage:
    calls: int = 0          # requests sent to the provider (retries included)
    completions: int = 0    # successful completions from the network
    cache_hits: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    retries: int = 0
    wait_s: float = 0.0     # time spent pacing below the free limits

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def check_messages(messages: list) -> None:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list of {role, content}")
    for m in messages:
        if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant") \
                or not isinstance(m.get("content"), str):
            raise ValueError(f"bad message {m!r}: need role system|user|assistant and str content")


def parse_retry_after(resp: httpx.Response) -> Optional[float]:
    """Retry-After header (seconds), else a Gemini RetryInfo retryDelay ("6s") in the body."""
    h = resp.headers.get("retry-after")
    if h:
        try:
            return max(0.0, float(h))
        except ValueError:
            pass
    try:
        for d in resp.json().get("error", {}).get("details", []) or []:
            if str(d.get("@type", "")).endswith("RetryInfo") and d.get("retryDelay"):
                return max(0.0, float(str(d["retryDelay"]).rstrip("s")))
    except (ValueError, AttributeError, TypeError):
        pass
    return None


class BaseClient:
    provider = "base"

    @property
    def cache_tag(self) -> str:
        """Provider name in the cache key; a provider adds any non-default setting that changes output."""
        return self.provider

    def __init__(self, *, cache_dir: os.PathLike = "runs/cache", quota_dir: os.PathLike = "runs/cache/_quota",
                 usage_log: Optional[os.PathLike] = "runs/usage/llm_calls.jsonl", replay_only: bool = False,
                 timeout: float = 150.0, max_retries: int = 6, backoff_base: float = 2.0,
                 backoff_cap: float = 90.0, http: Optional[httpx.Client] = None,
                 limits: Optional[dict] = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.time, rng: Optional[random.Random] = None):
        self.cache = ResponseCache(cache_dir)
        self.quota_dir = Path(quota_dir)
        self.usage_log = Path(usage_log) if usage_log else None
        self.replay_only = replay_only
        self.max_retries, self.backoff_base, self.backoff_cap = max_retries, backoff_base, backoff_cap
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0))
        self._limits_override = limits or {}
        self.sleep, self.clock = sleep, clock
        self.rng = rng or random.Random()
        self.usage = Usage()
        self._lock = threading.Lock()
        self._governors: dict = {}
        self.last_usage: Optional[dict] = None   # {"input_tokens", "output_tokens"} of the last complete()
        self._ratio: dict = {}     # per model: largest seen (actual input tokens / estimate)

    # ---- hooks for providers ----------------------------------------------------
    def _build(self, messages, model, temperature, seed, max_tokens) -> httpx.Request:
        raise NotImplementedError

    def _parse(self, resp: httpx.Response) -> Result:
        raise NotImplementedError

    def _neurons(self, lim: Limits, in_tok: Optional[int], out_tok: Optional[int]) -> Optional[float]:
        if not lim.neurons_per_day or in_tok is None or out_tok is None:
            return None
        return in_tok * lim.neurons_in_per_m / 1e6 + out_tok * lim.neurons_out_per_m / 1e6

    # ---- shared ---------------------------------------------------------------------
    def limits(self, model: str) -> Limits:
        return self._limits_override.get(model) or limits_for(self.provider, model)

    def governor(self, model: str) -> Governor:
        if model not in self._governors:
            self._governors[model] = Governor(self.provider, model, self.limits(model), self.quota_dir,
                                              clock=self.clock, sleep=self.sleep)
        return self._governors[model]

    def _log(self, rec: dict) -> None:
        if not self.usage_log:
            return
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        with open(self.usage_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def complete(self, messages: list, *, model: str, temperature: float, seed: int,
                 max_tokens: int = 512) -> str:
        check_messages(messages)
        self.last_usage = None
        key = request_key(self.cache_tag, model, messages, temperature, seed, max_tokens)
        hit = self.cache.get(key)
        base = {"provider": self.provider, "model": model, "key": key,
                "ts": datetime.now(CT).isoformat(timespec="seconds")}
        if hit is not None:
            with self._lock:
                self.usage.cache_hits += 1
            self._log({**base, "cached": True})
            # a hit reports the usage of the original call, so a replayed episode logs the same numbers
            self.last_usage = {"input_tokens": hit.get("input_tokens"), "output_tokens": hit.get("output_tokens")}
            return hit["text"]
        if self.replay_only:
            raise CacheMiss(f"replay_only: no cached response for {self.provider}:{model} key {key}")

        lim = self.limits(model)
        gov = self.governor(model)
        prompt_chars = "".join(m["content"] for m in messages)
        raw_est = estimate_tokens(prompt_chars) + 8 * len(messages)
        # if the provider counted more tokens than estimated before, scale estimates up
        est_in = int(raw_est * max(1.0, self._ratio.get(model, 1.0))) + 1
        est_neurons = self._neurons(lim, est_in, max_tokens) or 0.0
        t0 = time.monotonic()
        attempts, last_err, retry_why = 0, None, []
        while attempts <= self.max_retries:
            waited = gov.acquire(est_in, est_neurons)   # may raise QuotaExhausted (nothing sent)
            attempts += 1
            with self._lock:
                self.usage.calls += 1
                self.usage.wait_s += waited
            req = self._build(messages, model, temperature, seed, max_tokens)
            status, delay = None, None
            try:
                resp = self.http.send(req)
                status = resp.status_code
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = f"{type(e).__name__}: {e}"
                retry_why.append(type(e).__name__)
                # a dropped call may still have run upstream: keep its reserved tokens/neurons
            else:
                if status == 200:
                    res = self._parse(resp)
                    neurons = self._neurons(lim, res.input_tokens, res.output_tokens)
                    if res.input_tokens:
                        with self._lock:
                            self._ratio[model] = max(self._ratio.get(model, 1.0), res.input_tokens / raw_est)
                    gov.settle(res.input_tokens, neurons, est_in, est_neurons)
                    with self._lock:
                        self.usage.completions += 1
                        self.usage.input_tokens += res.input_tokens or 0
                        self.usage.output_tokens += res.output_tokens or 0
                    rec = {**base, "cached": False, "attempts": attempts, "retried_on": retry_why,
                           "finish_reason": res.finish_reason,
                           "latency_ms": int((time.monotonic() - t0) * 1000),
                           "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                           "neurons": round(neurons, 3) if neurons is not None else None,
                           "temperature": temperature, "seed": seed, "max_tokens": max_tokens}
                    self.cache.put(key, {"request": {"provider": self.provider, "model": model,
                                                     "messages": messages, "temperature": temperature,
                                                     "seed": seed, "max_tokens": max_tokens},
                                         "text": res.text, "usage": res.raw_usage, "finish_reason": res.finish_reason,
                                         "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                                         "ts": base["ts"]})
                    self._log(rec)
                    self.last_usage = {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens}
                    return res.text
                last_err = f"HTTP {status}: {resp.text[:500]}"
                if status not in RETRY_STATUS:
                    gov.settle(None, 0.0, est_in, est_neurons)
                    self._log({**base, "cached": False, "error": last_err[:300], "attempts": attempts})
                    raise LLMError(f"{self.provider}:{model} {last_err}", status)
                # a 5xx was not processed: release its input-token reservation (it still counts as a
                # request in the per-minute window); a 429 keeps it, to back off harder
                gov.settle(0 if status >= 500 else None, 0.0, est_in, est_neurons)
                retry_why.append(status)
                delay = parse_retry_after(resp)
            if attempts > self.max_retries:
                break
            backoff = min(self.backoff_cap, self.backoff_base * 2 ** (attempts - 1))
            backoff = backoff * (0.5 + self.rng.random() / 2)        # jitter in [50%, 100%]
            wait = max(backoff, delay + self.rng.random() if delay is not None else 0.0)
            with self._lock:
                self.usage.retries += 1
            self.sleep(wait)
        self._log({**base, "cached": False, "error": (last_err or "")[:300], "attempts": attempts,
                   "retried_on": retry_why})
        raise LLMError(f"{self.provider}:{model} failed after {attempts} attempts: {last_err}")
