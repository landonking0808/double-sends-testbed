#!/usr/bin/env python3
"""Read-only provider check: no completions, nothing billable.

  GEMINI_API_KEY_FILE=... CLOUDFLARE_API_TOKEN_FILE=... CLOUDFLARE_ACCOUNT_ID_FILE=... \\
      python scripts/check_providers.py

* Gemini: lists models (free endpoint) and reports whether the recommended Gemma 4 models are served.
* Cloudflare: verifies the token and that the recommended Workers AI models exist (model search).
* NIM (optional, an OpenAI-compatible endpoint given by NIM_BASE_URL): GET /models only.
Credentials come from the environment or *_FILE paths and are never printed.
"""
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dst.llm.limits import CLOUDFLARE_LIMITS, GEMINI_LIMITS  # noqa: E402
from dst.llm.providers import _secret  # noqa: E402


def check_gemini(http):
    try:
        key = _secret("GEMINI_API_KEY")
    except Exception as e:
        return f"gemini: SKIP ({e})"
    r = http.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 1000},
                 headers={"x-goog-api-key": key})
    if r.status_code != 200:
        return f"gemini: HTTP {r.status_code}"
    names = {m["name"].split("/", 1)[1] for m in r.json().get("models", [])}
    return "gemini: " + ", ".join(f"{m}={'served' if m in names else 'MISSING'}" for m in GEMINI_LIMITS)


def check_cloudflare(http):
    try:
        tok, acct = _secret("CLOUDFLARE_API_TOKEN"), _secret("CLOUDFLARE_ACCOUNT_ID")
    except Exception as e:
        return f"cloudflare: SKIP ({e})"
    h = {"Authorization": f"Bearer {tok}"}
    v = http.get("https://api.cloudflare.com/client/v4/user/tokens/verify", headers=h).json()
    out = [f"token {(v.get('result') or {}).get('status', 'invalid')}"]
    for m in CLOUDFLARE_LIMITS:
        r = http.get(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/models/search",
                     params={"search": m.split("/")[-1]}, headers=h)
        found = r.status_code == 200 and any(x.get("name") == m for x in r.json().get("result", []))
        out.append(f"{m}={'listed' if found else 'MISSING'}")
    return "cloudflare: " + ", ".join(out)


def check_nim(http):
    base = os.environ.get("NIM_BASE_URL")
    if not base:
        return "nim: SKIP (NIM_BASE_URL not set)"
    try:
        r = http.get(base.rstrip("/") + "/models")
        return f"nim: HTTP {r.status_code} {r.text[:120]}"
    except httpx.HTTPError as e:
        return f"nim: unreachable ({type(e).__name__})"


if __name__ == "__main__":
    with httpx.Client(timeout=30) as http:
        for f in (check_gemini, check_cloudflare, check_nim):
            print(f(http))
