"""Deterministic seed derivation.

Every random stream in an episode (task generation, faults, scheduler, board
staleness, LLM sampling seeds) is derived from the episode seed and a stream
name, so streams are independent of each other and stable across runs and
Python versions (no use of the salted built-in ``hash``).
"""
from __future__ import annotations

import hashlib
import random


def derive_seed(seed: int, *names: object) -> int:
    text = "|".join([str(int(seed))] + [str(n) for n in names])
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def make_rng(seed: int, *names: object) -> random.Random:
    return random.Random(derive_seed(seed, *names))
