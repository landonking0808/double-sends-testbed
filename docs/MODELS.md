# Models, free limits and the $0 evidence

Read and checked Fri Nov 6 2026, 7:03-7:20 AM CST (US Central). All experiments use free tiers only: no paid tier, no card, no paid sign-up. Every response is cached (`runs/cache/`), so a replay costs nothing.

## Recommendation

| | Provider | Exact model id | Family | Why |
|---|---|---|---|---|
| **Model A** | Gemini API (Google AI Studio key) | `gemma-4-26b-a4b-it` | Gemma 4 (open weights, MoE 26B / 4B active) | Free of charge on the Gemini API on every tier; 14.4K requests/day; best free capacity for the grid |
| **Model B** | Cloudflare Workers AI | `@cf/ibm-granite/granite-4.0-h-micro` | IBM Granite 4.0 (open weights, Apache-2.0) | A different family; the cheapest text model in neurons, so the free 10,000 neurons/day go furthest |
| Stretch (larger model) | Gemini API | `gemma-4-31b-it` | Gemma 4 dense 31B | Own per-model quota (same free limits as A) |

Capacity for Saturday (assuming about 1,500 input and 80 output tokens per agent call; the client holds every rate at 80% and every daily quota at 90%):

* **A, `gemma-4-26b-a4b-it`**: the binding limit is 16K input tokens/minute. At 80% (12.8K/min) that is about 8.5 calls/min, about 510 calls/hour, so 3,500 calls take about 7 hours; the daily cap (90% of 14.4K = 12,960) is far away. Shorter prompts go faster (800 tokens: about 16 calls/min).
* **B, `granite-4.0-h-micro`**: 1,500 x 1,542/1M + 80 x 10,158/1M = about 3.1 neurons a call, so the 9,000-neuron daily cap (90% of 10,000) gives about 2,900 calls per UTC day. The neuron day resets at 00:00 UTC = 6:00 PM CST, so a run that spans Saturday 6 PM CST can use two allocations (about 5,800 calls on Saturday Central time), and Friday's allocation is usable for the pilot.
* If B's quality is too low for the tool-call format, the next Workers AI options in neuron cost are `@cf/meta/llama-3.2-3b-instruct` (about 9.4 neurons a call, about 950 calls/day) and `@cf/meta/llama-3.2-1b-instruct` (about 5.1, about 1,750/day); both carry a planned deprecation date of 2026-11-15 in the model catalog (after the sprint).

Both models passed the smoke test (`scripts/smoke_llm.py`, 2 prompts each, valid JSON tool calls), Fri Nov 6 2026 7:18-7:19 AM CST.

## (a) Gemini API

Models listed by `GET /v1beta/models` with this key include `gemma-4-26b-a4b-it`, `gemma-4-31b-it`, `gemini-2.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-3.5-flash-lite`.

**Gemma 4: $0, usable.**

* Pricing page, https://ai.google.dev/gemini-api/docs/pricing, section "Gemma 4" (read Nov 6 2026): the Free Tier column says "Input price: Free of charge", "Output price: Free of charge", and the Paid Tier column says "Not available" for input and output. Gemma 4 has no paid price at all, so its use costs $0 whatever the project's billing state.
* This key's own quota: an oversized request (one call, about 20K input tokens) to `gemma-4-26b-a4b-it` returned HTTP 429 with `"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_input_token_count"`, `"quotaId": "GenerateContentInputTokensPerModelPerMinute-FreeTier"`, `"quotaValue": "16000"` and the message "Generate content input tokens per minute limit exceeded on Free Tier" (Nov 6 2026, 7:10 AM CST).
* Limits: the docs page no longer lists per-model numbers ("Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google AI Studio"). The AI Studio rate-limit page (https://aistudio.google.com/rate-limit) of a free-tier project ("Gemini API Rate Limit / Free tier / Set up billing"), read Nov 6 2026, shows:

| Model | RPM | TPM (input) | RPD |
|---|---|---|---|
| Gemma 4 26B (`gemma-4-26b-a4b-it`) | 30 | 16K | 14.4K |
| Gemma 4 31B (`gemma-4-31b-it`) | 30 | 16K | 14.4K |

  The 16K TPM matches the 429 above for this key. From https://ai.google.dev/gemini-api/docs/rate-limits: "Rate limits are applied per project, not per API key. Requests per day (RPD) quotas reset at midnight Pacific time."

**Flash-Lite: not used.**

* The pricing page lists a Free Tier ("Free of charge") and a paid price for each Flash-Lite model (for example `gemini-2.5-flash-lite` $0.10 input / $0.40 output per 1M tokens; `gemini-3.1-flash-lite` $0.25 / $1.50). Whether a call is free depends on whether the project has billing linked: "Tier 1: Set up and link an active billing account" (rate-limits page, Usage tiers).
* Billing on the key's project could not be ruled out: an API key cannot read the billing state, the project is not visible from the Google account that was checked, and an invalid-request probe returned only a validation error. So Flash-Lite is treated as **not usable** for $0.
* Even on a confirmed free tier it could not carry the grid: the same AI Studio page shows Gemini 2.5 Flash Lite at 10 RPM / 250K TPM / **20 RPD**, Gemini 3.1 Flash Lite and Gemini 3.5 Flash Lite at 15 RPM / 250K TPM / **500 RPD**.
* The client has no limits entry for any Flash-Lite model, so `complete()` refuses it (`KeyError: no recorded free-tier limits`) before any request.

## (b) Cloudflare Workers AI

**$0, usable, with the client's hard cap.**

* Pricing, https://developers.cloudflare.com/workers-ai/platform/pricing/ (read Nov 6 2026): "Our free allocation allows anyone to use a total of 10,000 Neurons per day at no charge. To use more than 10,000 Neurons per day, you need to sign up for the Workers Paid plan." Table: "Workers Free | 10,000 Neurons per day | N/A - Upgrade to Workers Paid"; "Workers Paid | 10,000 Neurons per day | $0.011 / 1,000 Neurons".
* Plan: the account was created on Oct 29 2026 on the Free plan with no payment card on file (account record kept with the credentials). The API token is scoped to Workers AI only, so `GET /accounts/{id}/subscriptions` answers 403 and the plan cannot be re-read through the API. The $0 guarantee therefore does not rest on the plan alone: both plans include the same 10,000 neurons/day at no charge, and the client refuses any call that would take the account past **9,000 neurons in the UTC day** (estimate reserved before the call, settled to the real token counts after). Under that cap usage is $0 on either plan.
* Limits, https://developers.cloudflare.com/workers-ai/platform/limits/: Text Generation "300 requests per minute, unless the model requires the Workers Paid plan". Neuron rates (pricing page): `@cf/ibm-granite/granite-4.0-h-micro` 1,542 neurons per 1M input tokens, 10,158 per 1M output tokens; `@cf/meta/llama-3.2-3b-instruct` 4,625 / 30,475; `@cf/meta/llama-3.2-1b-instruct` 2,457 / 18,252. The allocation is account-wide (all models share one counter in the client).
* Models that "require a paid billing method" (the page lists `kimi-k2.6`, `glm-5.x`, `deepseek-v4*`) are never used.
* Token check: `GET /user/tokens/verify` "This API Token is valid and active". `@cf/meta/llama-3.1-8b-instruct-fp8-fast` is on the pricing page but not in this account's model catalog, so it is not used.

## (c) NVIDIA NIM (local OpenAI-compatible endpoint)

**Not used.** A local NIM endpoint on one NVIDIA account's free tier exists on the host, but it is switched off by its operator: `GET /v1/models` answers HTTP 503 "NVIDIA NIM is turned off". It was not turned back on, and no other NVIDIA account is used.

## How the client stays at $0

* `dst/llm/limits.py` holds every free limit above; a model without an entry cannot be called.
* Requests and input tokens per minute: a sliding 60-second window at 80% of RPM and TPM (stricter than a token bucket: no burst can pass the limit in any minute), shared across processes through a locked state file.
* Daily: a persisted counter refuses the call that would pass 90% of RPD (Gemini, reset at midnight Pacific) or 90% of the neuron allocation (Cloudflare, reset 00:00 UTC). Refused calls raise `QuotaExhausted` and are never sent.
* 429 and 5xx: exponential backoff with jitter, honoring `Retry-After` and Google's `RetryInfo.retryDelay`.
* State: `runs/cache/` (responses) and `runs/cache/_quota/` (counters), gitignored; per-call usage in `runs/usage/llm_calls.jsonl` (no prompts, no credentials).
* Gemma 4 thinks by default and its thoughts count against `maxOutputTokens` (a 64-token budget once ended with an empty answer); the Gemini client sends `thinkingConfig: {"thinkingLevel": "minimal"}` by default (`thinkingBudget` is rejected for Gemma). Workers AI requires `seed >= 1`, so testbed seed `s` is sent as `s + 1`; the cache key keeps `s`.

## Client interface

```python
from dst.llm import get_client, MODEL_A, MODEL_B
llm = get_client("gemini")            # "cloudflare", "fake"; replay_only=True never touches the network
text = llm.complete(messages, model="gemma-4-26b-a4b-it", temperature=0.7, seed=7, max_tokens=512)
llm.last_usage   # {"input_tokens": ..., "output_tokens": ...}, the same on a cache hit
llm.usage        # calls, completions, cache_hits, input/output tokens, retries, pacing wait
```

Credentials come from `GEMINI_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID` (or `*_FILE` paths). `scripts/check_providers.py` is a read-only check (no completions); `scripts/smoke_llm.py` sends 2 tiny prompts per recommended model.
