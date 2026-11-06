"""Free-tier limits, a cross-process rate limiter and a persisted daily quota counter.

Every limit below comes from the provider's official docs or from the provider's own
429 body (see docs/MODELS.md for the source of each number). The client never runs at
the published limit: requests and input tokens per minute are held at ``RATE_FRACTION``
(80%) of the limit, and the daily counter refuses a call that would pass
``DAILY_FRACTION`` (90%) of the daily limit. A refused call raises ``QuotaExhausted``;
it is never sent.

State lives in small JSON files under a quota directory (default ``runs/cache/_quota/``, gitignored), each
guarded by an exclusive ``fcntl`` lock, so several worker processes share one budget.
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

RATE_FRACTION = 0.8
DAILY_FRACTION = 0.9
WINDOW_S = 60.0


class QuotaExhausted(RuntimeError):
    """The daily free quota (at DAILY_FRACTION) would be passed; the call was not sent."""


@dataclass(frozen=True)
class Limits:
    rpm: Optional[int] = None          # requests per minute
    tpm: Optional[int] = None          # input tokens per minute
    rpd: Optional[int] = None          # requests per day
    neurons_per_day: Optional[int] = None   # Cloudflare: account-wide daily free allocation
    neurons_in_per_m: float = 0.0      # Cloudflare: neurons per 1M input tokens
    neurons_out_per_m: float = 0.0     # Cloudflare: neurons per 1M output tokens
    reset_tz: str = "UTC"              # zone whose midnight resets the daily quota


# Gemini API free tier. The docs page no longer prints per-model numbers ("can be viewed in
# Google AI Studio"); the AI Studio rate-limit page of a free-tier project showed, on Nov 6 2026,
# Gemma 4 26B and Gemma 4 31B at 30 RPM / 16K TPM / 14.4K RPD, and this testbed's own key got a
# 429 naming quotaValue 16000 for GenerateContentInputTokensPerModelPerMinute-FreeTier. RPD resets
# at midnight Pacific time (ai.google.dev/gemini-api/docs/rate-limits). Flash-Lite models are left
# out on purpose: their free RPD (20-500) cannot carry a run, and they have a paid price.
GEMINI_LIMITS = {
    "gemma-4-26b-a4b-it": Limits(rpm=30, tpm=16000, rpd=14400, reset_tz="America/Los_Angeles"),
    "gemma-4-31b-it": Limits(rpm=30, tpm=16000, rpd=14400, reset_tz="America/Los_Angeles"),
}

# Cloudflare Workers AI: 300 requests/minute for text generation, 10,000 neurons/day free,
# reset 00:00 UTC; neuron rates from developers.cloudflare.com/workers-ai/platform/pricing/.
# The Llama 3.2 models carry a planned deprecation date of 2026-11-15 in the model catalog.
CF_NEURONS_PER_DAY = 10000
CLOUDFLARE_LIMITS = {
    "@cf/meta/llama-3.2-3b-instruct": Limits(rpm=300, neurons_per_day=CF_NEURONS_PER_DAY,
                                             neurons_in_per_m=4625, neurons_out_per_m=30475),
    "@cf/meta/llama-3.2-1b-instruct": Limits(rpm=300, neurons_per_day=CF_NEURONS_PER_DAY,
                                             neurons_in_per_m=2457, neurons_out_per_m=18252),
    "@cf/ibm-granite/granite-4.0-h-micro": Limits(rpm=300, neurons_per_day=CF_NEURONS_PER_DAY,
                                                  neurons_in_per_m=1542, neurons_out_per_m=10158),
}

PROVIDER_LIMITS = {"gemini": GEMINI_LIMITS, "cloudflare": CLOUDFLARE_LIMITS}


def limits_for(provider: str, model: str) -> Limits:
    table = PROVIDER_LIMITS.get(provider, {})
    if model not in table:
        raise KeyError(f"no recorded free-tier limits for {provider}:{model}; add them to "
                       f"dst/llm/limits.py (from the official docs) before calling it")
    return table[model]


def estimate_tokens(text: str) -> int:
    """Conservative token estimate (about 3.5 characters a token) used before a call."""
    return max(1, math.ceil(len(text) / 3.5))


def day_key(tz: str, now: Optional[float] = None) -> str:
    t = datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)
    return t.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)


@contextmanager
def _locked_json(path: Path):
    """Yield (state dict) under an exclusive lock; the dict is written back on exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read()
            try:
                state = json.loads(raw) if raw.strip() else {}
            except ValueError:
                state = {}
            yield state
            f.seek(0)
            f.truncate()
            f.write(json.dumps(state, sort_keys=True))
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Governor:
    """Per (provider, model) pacing and daily accounting, shared across processes.

    * ``acquire(est_tokens, est_neurons)`` blocks until the sliding 60 s window has room
      for one more request and ``est_tokens`` input tokens at 80% of RPM/TPM (a sliding
      window is stricter than a token bucket: no burst can pass the limit in any minute),
      then checks the daily counters at 90% and records the request. Raises QuotaExhausted.
    * ``settle(actual_tokens, actual_neurons, reserved_tokens, reserved_neurons)`` replaces the
      estimates with the provider's real usage once the response is in.
    """

    def __init__(self, provider: str, model: str, limits: Limits, quota_dir: os.PathLike,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep):
        self.provider, self.model, self.limits = provider, model, limits
        self.quota_dir = Path(quota_dir)
        self.path = self.quota_dir / f"{_safe(provider)}__{_safe(model)}.json"
        # Cloudflare's neuron allocation is account-wide: one file for every model.
        self.neuron_path = self.quota_dir / f"{_safe(provider)}__neurons.json"
        self.clock, self.sleep = clock, sleep

    # ---- daily -------------------------------------------------------------------
    def calls_today(self) -> int:
        with _locked_json(self.path) as st:
            return st.get("calls", 0) if st.get("day") == day_key(self.limits.reset_tz, self.clock()) else 0

    def neurons_today(self) -> float:
        with _locked_json(self.neuron_path) as st:
            return st.get("neurons", 0.0) if st.get("day") == day_key("UTC", self.clock()) else 0.0

    def _check_neurons(self, est_neurons: float) -> None:
        lim = self.limits.neurons_per_day
        if not lim:
            return
        cap = lim * DAILY_FRACTION
        with _locked_json(self.neuron_path) as st:
            today = day_key("UTC", self.clock())
            if st.get("day") != today:
                st.clear()
                st.update(day=today, neurons=0.0)
            if st["neurons"] + est_neurons > cap:
                raise QuotaExhausted(f"{self.provider}: {st['neurons']:.0f} neurons used today, "
                                     f"+{est_neurons:.0f} would pass {cap:.0f} (90% of {lim})")
            st["neurons"] += est_neurons

    def settle(self, actual_tokens: Optional[int], actual_neurons: Optional[float],
               reserved_tokens: int, reserved_neurons: float) -> None:
        if actual_tokens is not None and self.limits.tpm:
            with _locked_json(self.path) as st:
                win = st.get("window", [])
                for i in range(len(win) - 1, -1, -1):
                    if win[i][1] == reserved_tokens:
                        win[i][1] = actual_tokens
                        break
        if actual_neurons is not None and self.limits.neurons_per_day:
            with _locked_json(self.neuron_path) as st:
                if st.get("day") == day_key("UTC", self.clock()):
                    st["neurons"] = max(0.0, st.get("neurons", 0.0) - reserved_neurons + actual_neurons)

    # ---- per minute + per day -----------------------------------------------------
    def acquire(self, est_tokens: int = 0, est_neurons: float = 0.0) -> float:
        """Wait for room, then record one request. Returns the seconds waited."""
        lim = self.limits
        rpm = lim.rpm * RATE_FRACTION if lim.rpm else None
        tpm = lim.tpm * RATE_FRACTION if lim.tpm else None
        if tpm is not None and est_tokens > tpm:
            raise QuotaExhausted(f"{self.provider}:{self.model}: one request of ~{est_tokens} input "
                                 f"tokens is over 80% of the {lim.tpm} TPM free limit")
        waited = 0.0
        while True:
            with _locked_json(self.path) as st:
                now = self.clock()
                today = day_key(lim.reset_tz, now)
                if st.get("day") != today:
                    st["day"], st["calls"] = today, 0
                win = [w for w in st.get("window", []) if now - w[0] < WINDOW_S]
                st["window"] = win
                if lim.rpd and st["calls"] + 1 > lim.rpd * DAILY_FRACTION:
                    raise QuotaExhausted(f"{self.provider}:{self.model}: {st['calls']} calls today "
                                         f"(day {today} {lim.reset_tz}); the next would pass "
                                         f"{int(lim.rpd * DAILY_FRACTION)} (90% of {lim.rpd} RPD)")
                n_ok = rpm is None or len(win) + 1 <= rpm
                t_ok = tpm is None or sum(w[1] for w in win) + est_tokens <= tpm
                if n_ok and t_ok:
                    if est_neurons:
                        self._check_neurons(est_neurons)   # raises before anything is recorded
                    win.append([now, est_tokens])
                    st["calls"] += 1
                    st["total_calls"] = st.get("total_calls", 0) + 1
                    return waited
                # earliest moment the window has room again
                if not n_ok:
                    wait = win[len(win) - int(rpm)][0] + WINDOW_S - now if rpm >= 1 else WINDOW_S
                else:
                    need, acc, wait = sum(w[1] for w in win) + est_tokens - tpm, 0, WINDOW_S
                    for ts, tok in win:
                        acc += tok
                        if acc >= need:
                            wait = ts + WINDOW_S - now
                            break
            wait = max(0.05, wait)
            self.sleep(wait)
            waited += wait
