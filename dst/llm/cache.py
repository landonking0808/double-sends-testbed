"""On-disk response cache: one JSON file per request, keyed by a sha256 of the request.

The key covers exactly (provider, model, messages, temperature, seed, max_tokens), in a
canonical JSON form, so the same request always maps to the same file and a replay
reads back byte-identical text.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


class CacheMiss(KeyError):
    """replay_only mode and the response is not in the cache."""


def request_key(provider: str, model: str, messages: list, temperature: float, seed: int,
                max_tokens: int) -> str:
    canon = json.dumps({"provider": provider, "model": model,
                        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                        "temperature": float(temperature), "seed": int(seed),
                        "max_tokens": int(max_tokens)},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, root: os.PathLike = "runs/cache"):
        self.root = Path(root)

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Optional[dict]:
        p = self.path(key)
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def put(self, key: str, record: dict) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, sort_keys=True, ensure_ascii=False, indent=1)
        os.replace(tmp, p)   # atomic: a reader never sees half a file
