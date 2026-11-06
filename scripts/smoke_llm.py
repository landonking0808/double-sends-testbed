#!/usr/bin/env python3
"""Smoke test: 2 tiny real prompts per recommended model, each asking for one JSON tool call.

  GEMINI_API_KEY_FILE=... CLOUDFLARE_API_TOKEN_FILE=... CLOUDFLARE_ACCOUNT_ID_FILE=... \\
      python scripts/smoke_llm.py [--replay]

Responses go through the normal cache (runs/cache/), so a second run (or --replay) is free.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dst.llm import MODEL_A, MODEL_B, get_client  # noqa: E402

SYSTEM = ("You are an office worker agent. Reply with exactly one JSON object and nothing else, of the form "
          '{"tool": "<name>", "args": {...}}. Tools: read_inbox() takes no args; '
          "lookup(customer) takes a customer name.")
PROMPTS = [("Start your shift: read your inbox.", "read_inbox"),
           ("Look up the customer named Ada Park.", "lookup")]


def parse_tool(text):
    t = text.strip()
    m = re.search(r"\{.*\}", t, re.S)          # tolerate a ```json fence
    obj = json.loads(m.group(0) if m else t)
    assert isinstance(obj, dict) and isinstance(obj.get("tool"), str) and isinstance(obj.get("args"), dict)
    return obj


def main():
    replay = "--replay" in sys.argv
    ok = True
    for provider, model in (MODEL_A, MODEL_B):
        client = get_client(provider, replay_only=replay)
        for i, (prompt, want) in enumerate(PROMPTS):
            msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
            try:
                text = client.complete(msgs, model=model, temperature=0.0, seed=i, max_tokens=64)
                obj = parse_tool(text)
                good = obj["tool"] == want
                print(f"{'PASS' if good else 'WRONG-TOOL'} {provider}:{model} #{i} -> {json.dumps(obj)}")
                ok &= good
            except Exception as e:   # report and continue with the next prompt
                print(f"FAIL {provider}:{model} #{i}: {type(e).__name__}: {str(e)[:300]}")
                ok = False
        print(f"  usage {provider}:{model}: {client.usage.as_dict()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
