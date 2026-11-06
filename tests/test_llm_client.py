"""dst.llm: cache, replay, retries, pacing, daily quota, provider wire formats. No network."""
import json
import random

import httpx
import pytest

from dst.llm import (CacheMiss, CloudflareClient, FakeClient, GeminiClient, LLMError, Limits,
                     QuotaExhausted, get_client, request_key)
from dst.llm.limits import Governor, day_key

MSG = [{"role": "system", "content": "You are a worker."}, {"role": "user", "content": "Go."}]


class FakeTime:
    def __init__(self, t=1_800_000_000.0):
        self.t, self.sleeps = t, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def gemini_ok(text='{"tool":"read_inbox","args":{}}', pin=12, pout=7):
    return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}],
                                     "usageMetadata": {"promptTokenCount": pin, "candidatesTokenCount": pout}})


def make_gemini(tmp_path, handler, ft=None, limits=None, **kw):
    ft = ft or FakeTime()
    lim = limits or {"m": Limits(rpm=60, tpm=100000, rpd=1000, reset_tz="America/Los_Angeles")}
    return GeminiClient(api_key="k-test", http=httpx.Client(transport=httpx.MockTransport(handler)),
                        cache_dir=tmp_path / "cache", quota_dir=tmp_path / "quota",
                        usage_log=tmp_path / "usage.jsonl", limits=lim, sleep=ft.sleep, clock=ft.clock,
                        rng=random.Random(0), **kw), ft


def test_gemini_request_shape_and_cache(tmp_path):
    seen = []

    def h(req):
        seen.append(req)
        return gemini_ok()

    c, _ = make_gemini(tmp_path, h)
    out = c.complete(MSG, model="m", temperature=0.7, seed=3, max_tokens=64)
    assert json.loads(out) == {"tool": "read_inbox", "args": {}}
    req = seen[0]
    assert req.url.path == "/v1beta/models/m:generateContent"
    assert req.headers["x-goog-api-key"] == "k-test"
    body = json.loads(req.content)
    assert body["generationConfig"] == {"temperature": 0.7, "seed": 3, "maxOutputTokens": 64,
                                        "thinkingConfig": {"thinkingLevel": "minimal"}}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "You are a worker.\n\nGo."}]}]
    # second identical call: from the cache, no request
    assert c.complete(MSG, model="m", temperature=0.7, seed=3, max_tokens=64) == out
    assert len(seen) == 1
    assert c.usage.calls == 1 and c.usage.cache_hits == 1
    assert c.usage.input_tokens == 12 and c.usage.output_tokens == 7
    assert c.last_usage == {"input_tokens": 12, "output_tokens": 7}     # also on a cache hit
    # any change in a key field is a different request
    c.complete(MSG, model="m", temperature=0.7, seed=4, max_tokens=64)
    assert len(seen) == 2
    log = [json.loads(l) for l in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert [r["cached"] for r in log] == [False, True, False]
    assert "k-test" not in (tmp_path / "usage.jsonl").read_text()
    assert not any("k-test" in p.read_text() for p in (tmp_path / "cache").rglob("*.json"))


def test_replay_only_is_offline_and_byte_identical(tmp_path):
    c, _ = make_gemini(tmp_path, lambda r: gemini_ok("hello ✓"))
    live = c.complete(MSG, model="m", temperature=0.7, seed=1)

    def boom(req):
        raise AssertionError("replay must not touch the network")

    r, _ = make_gemini(tmp_path, boom, replay_only=True)
    assert r.complete(MSG, model="m", temperature=0.7, seed=1) == live
    with pytest.raises(CacheMiss):
        r.complete(MSG, model="m", temperature=0.7, seed=2)
    # get_client replay needs no credential
    g = get_client("gemini", replay_only=True, cache_dir=tmp_path / "cache", quota_dir=tmp_path / "q",
                   usage_log=None, limits={"m": Limits(rpm=1)})
    assert g.complete(MSG, model="m", temperature=0.7, seed=1) == live


def test_retry_on_429_honours_retry_info_then_succeeds(tmp_path):
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        if n["i"] == 1:
            return httpx.Response(429, json={"error": {"code": 429, "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})
        if n["i"] == 2:
            return httpx.Response(503, headers={"Retry-After": "3"}, text="busy")
        return gemini_ok()

    c, ft = make_gemini(tmp_path, h)
    assert c.complete(MSG, model="m", temperature=0, seed=0)
    assert n["i"] == 3 and c.usage.retries == 2 and c.usage.calls == 3
    assert ft.sleeps[0] >= 7 and ft.sleeps[1] >= 3


def test_retry_on_timeout_and_give_up(tmp_path):
    def h(req):
        raise httpx.ReadTimeout("slow", request=req)

    c, ft = make_gemini(tmp_path, h, max_retries=3)
    with pytest.raises(LLMError, match="after 4 attempts"):
        c.complete(MSG, model="m", temperature=0, seed=0)
    # exponential with jitter: each wait within [50%, 100%] of 2, 4, 8
    assert len(ft.sleeps) == 3
    for s, full in zip(ft.sleeps, [2, 4, 8]):
        assert full / 2 <= s <= full


def test_non_retryable_error_raises_at_once(tmp_path):
    c, _ = make_gemini(tmp_path, lambda r: httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(LLMError) as e:
        c.complete(MSG, model="m", temperature=0, seed=0)
    assert e.value.status == 400 and c.usage.calls == 1


def test_rpm_pacing_stays_below_80_percent(tmp_path):
    ft = FakeTime()
    sent = []

    def h(req):
        sent.append(ft.t)
        return gemini_ok()

    c, _ = make_gemini(tmp_path, h, ft=ft, limits={"m": Limits(rpm=10, rpd=1000)})
    for s in range(20):
        c.complete(MSG, model="m", temperature=0, seed=s)
    # rpm 10 -> at most 8 in any 60 s window
    for i, t in enumerate(sent):
        assert sum(1 for u in sent if t <= u < t + 60) <= 8
    assert c.usage.wait_s > 0


def test_tpm_pacing_and_oversized_request(tmp_path):
    ft = FakeTime()
    sent = []

    def h(req):
        sent.append(ft.t)
        return gemini_ok(pin=400)

    lim = {"m": Limits(rpm=1000, tpm=1000, rpd=1000)}
    c, _ = make_gemini(tmp_path, h, ft=ft, limits=lim)
    big = [{"role": "user", "content": "x" * 1300}]        # ~372 estimated, 400 actual tokens
    for s in range(4):
        c.complete(big, model="m", temperature=0, seed=s)
    # 80% of 1000 = 800 tokens a minute: two 400-token calls per window at most
    for t in sent:
        assert sum(1 for u in sent if t <= u < t + 60) <= 2
    with pytest.raises(QuotaExhausted):
        c.complete([{"role": "user", "content": "x" * 5000}], model="m", temperature=0, seed=9)


def test_daily_cap_refuses_at_90_percent_and_persists(tmp_path):
    ft = FakeTime()
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return gemini_ok()

    lim = {"m": Limits(rpm=1000, rpd=10, reset_tz="America/Los_Angeles")}
    c, _ = make_gemini(tmp_path, h, ft=ft, limits=lim)
    for s in range(9):
        c.complete(MSG, model="m", temperature=0, seed=s)
    with pytest.raises(QuotaExhausted, match="90% of 10 RPD"):
        c.complete(MSG, model="m", temperature=0, seed=99)
    assert n["i"] == 9
    # a new process (new client, same quota dir) sees the same count
    c2, _ = make_gemini(tmp_path, h, ft=ft, limits=lim)
    with pytest.raises(QuotaExhausted):
        c2.complete(MSG, model="m", temperature=0, seed=100)
    # cached answers still work when the quota is spent
    assert c2.complete(MSG, model="m", temperature=0, seed=0)
    # the next Pacific day resets it
    ft.t += 24 * 3600
    c2.complete(MSG, model="m", temperature=0, seed=101)
    assert n["i"] == 10


def test_day_key_uses_reset_zone():
    t = 1_800_000_000.0 - (1_800_000_000 % 86400) + 3 * 3600   # 03:00 UTC
    assert day_key("UTC", t) != day_key("America/Los_Angeles", t)


def cf_handler(seen, pin=1000, pout=100):
    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"success": True, "errors": [], "result": {
            "response": "ok", "usage": {"prompt_tokens": pin, "completion_tokens": pout}}})
    return h


def test_cloudflare_shape_and_neuron_budget(tmp_path):
    seen = []
    ft = FakeTime()
    # 1000 in + 100 out at 4119 / 34868 per M = 7.6 neurons a call; budget 100 -> cap 90
    lim = {"@cf/x": Limits(rpm=300, neurons_per_day=100, neurons_in_per_m=4119, neurons_out_per_m=34868)}
    c = CloudflareClient(api_token="t-test", account_id="acct1", http=httpx.Client(
        transport=httpx.MockTransport(cf_handler(seen))), cache_dir=tmp_path / "c", quota_dir=tmp_path / "q",
        usage_log=tmp_path / "u.jsonl", limits=lim, sleep=ft.sleep, clock=ft.clock)
    assert c.complete(MSG, model="@cf/x", temperature=0.7, seed=5, max_tokens=100) == "ok"
    req = seen[0]
    assert req.url.path == "/client/v4/accounts/acct1/ai/run/@cf/x"
    assert req.headers["authorization"] == "Bearer t-test"
    # Workers AI needs seed >= 1: seed s goes out as s + 1
    assert json.loads(req.content) == {"messages": MSG, "temperature": 0.7, "seed": 6, "max_tokens": 100}
    gov = c.governor("@cf/x")
    assert abs(gov.neurons_today() - 7.6058) < 0.01          # settled to the real usage
    with pytest.raises(QuotaExhausted, match="neurons"):
        for s in range(1, 20):
            c.complete(MSG, model="@cf/x", temperature=0.7, seed=s, max_tokens=100)
    # after the first call the client scales its estimates to the real token counts,
    # so it stops at 90% of the allocation, never past it
    assert gov.neurons_today() <= 90
    assert len(seen) == 11                                   # 11 x 7.6 = 83.7; a 12th would pass 90


def test_gemini_role_mapping_and_thoughts():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"},
            {"role": "user", "content": "d"}]
    assert GeminiClient.to_contents(msgs) == [
        {"role": "user", "parts": [{"text": "S\n\na"}]}, {"role": "model", "parts": [{"text": "b"}]},
        {"role": "user", "parts": [{"text": "c\n\nd"}]}]
    c = GeminiClient.__new__(GeminiClient)
    r = c._parse(httpx.Response(200, json={"candidates": [{"content": {"parts": [
        {"text": "thinking", "thought": True}, {"text": "answer"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "thoughtsTokenCount": 3}}))
    assert r.text == "answer" and r.input_tokens == 5 and r.output_tokens == 5
    only_thoughts = c._parse(httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS",
        "content": {"parts": [{"text": "t", "thought": True}]}}],
        "usageMetadata": {"promptTokenCount": 5, "thoughtsTokenCount": 3}}))
    assert only_thoughts.text == "" and only_thoughts.output_tokens == 3
    assert only_thoughts.finish_reason == "MAX_TOKENS"


def test_thinking_level_is_part_of_cache_key(tmp_path):
    seen = []
    c, _ = make_gemini(tmp_path, lambda r: (seen.append(r), gemini_ok())[1])
    c.complete(MSG, model="m", temperature=0, seed=0)
    h, _ = make_gemini(tmp_path, lambda r: (seen.append(r), gemini_ok())[1], thinking_level="high")
    h.complete(MSG, model="m", temperature=0, seed=0)
    assert len(seen) == 2
    assert json.loads(seen[1].content)["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "high"}


def test_request_key_is_canonical():
    a = request_key("p", "m", [{"content": "x", "role": "user"}], 0.7, 1, 512)
    b = request_key("p", "m", [{"role": "user", "content": "x"}], 0.70, 1, 512)
    assert a == b and len(a) == 64
    assert a != request_key("p", "m", [{"role": "user", "content": "x"}], 0.7, 1, 256)


def test_fake_client_and_cache(tmp_path):
    f = get_client("fake", responder=["one", "two"])
    assert [f.complete(MSG, model="x", temperature=0, seed=s) for s in range(3)] == ["one", "two", "one"]
    assert len(f.calls) == 3 and f.calls[0]["seed"] == 0
    g = FakeClient(lambda m, p: f"seed={p['seed']}", cache_dir=tmp_path)
    assert g.complete(MSG, seed=4) == "seed=4"
    r = FakeClient("unused", cache_dir=tmp_path, replay_only=True)
    assert r.complete(MSG, seed=4) == "seed=4"
    with pytest.raises(CacheMiss):
        r.complete(MSG, seed=5)


def test_unknown_model_without_limits_is_refused(tmp_path):
    c = GeminiClient(api_key="k", http=httpx.Client(transport=httpx.MockTransport(lambda r: gemini_ok())),
                     cache_dir=tmp_path / "c", quota_dir=tmp_path / "q", usage_log=None)
    with pytest.raises(KeyError, match="no recorded free-tier limits"):
        c.complete(MSG, model="gemini-2.5-flash-lite", temperature=0, seed=0)


def test_bad_message_rejected():
    with pytest.raises(ValueError):
        FakeClient().complete([{"role": "tool", "content": "x"}])
