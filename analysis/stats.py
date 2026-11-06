"""Statistics used by the analysis. All randomness is seeded and local."""
from __future__ import annotations

import math
import zlib

import numpy as np

from dst.metrics import wilson  # one Wilson implementation for the whole repo

N_BOOT = 5000
N_PERM = 10000


def sub_seed(base: int, *key) -> int:
    """A stable per-cell seed (independent of Python's hash randomization)."""
    return (int(base) * 1_000_003 + zlib.crc32("|".join(map(str, key)).encode())) % (2 ** 32)


def rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": int(k), "n": int(n), "rate": (k / n) if n else float("nan"), "lo": lo, "hi": hi}


def cluster_bootstrap_ratio(num: dict, den: dict, n_boot: int = N_BOOT, seed: int = 0, scale: float = 1.0) -> tuple:
    """Percentile 95% interval for scale * sum(num) / sum(den), resampling clusters (seeds).

    ``num`` and ``den`` map cluster id -> summed numerator / denominator of that cluster.
    """
    keys = sorted(num)
    if not keys:
        return (float("nan"), float("nan"))
    a = np.array([num[k] for k in keys], dtype=float)
    b = np.array([den[k] for k in keys], dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(n_boot, len(keys)))
    tot_b = b[idx].sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        stats = scale * a[idx].sum(axis=1) / tot_b
    stats = stats[np.isfinite(stats)]
    if stats.size == 0:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return (float(lo), float(hi))


def fisher_exact(k1: int, n1: int, k2: int, n2: int) -> dict:
    """Two-sided Fisher exact test on [[k1, n1-k1], [k2, n2-k2]]."""
    from scipy.stats import fisher_exact as _fe
    if n1 == 0 or n2 == 0:
        return {"p": float("nan"), "odds_ratio": float("nan")}
    odds, p = _fe([[k1, n1 - k1], [k2, n2 - k2]], alternative="two-sided")
    return {"p": float(p), "odds_ratio": float(odds)}


def permutation_test(x: list, y: list, strata_x: list | None = None, strata_y: list | None = None,
                     n_perm: int = N_PERM, seed: int = 0) -> dict:
    """Two-sided permutation test for mean(y) - mean(x). Labels are permuted within strata
    (e.g. tree shape) when strata are given. p = (1 + #{|T*| >= |T|}) / (1 + n_perm)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size == 0 or y.size == 0:
        return {"p": float("nan"), "diff": float("nan")}
    obs = float(y.mean() - x.mean())
    vals = np.concatenate([x, y])
    is_y = np.concatenate([np.zeros(x.size, bool), np.ones(y.size, bool)])
    if strata_x is None:
        strata = np.zeros(vals.size, dtype=int)
    else:
        labels = sorted(set(strata_x) | set(strata_y))
        strata = np.array([labels.index(s) for s in list(strata_x) + list(strata_y)])
    if np.all(vals == vals[0]):
        return {"p": 1.0, "diff": obs}
    rng = np.random.default_rng(seed)
    lab = np.empty((n_perm, vals.size), dtype=bool)
    for s in np.unique(strata):
        g = np.flatnonzero(strata == s)
        lab[:, g] = rng.permuted(np.tile(is_y[g], (n_perm, 1)), axis=1)
    sum_y = (lab * vals).sum(axis=1)
    t = sum_y / y.size - (vals.sum() - sum_y) / x.size
    count = int(np.sum(np.abs(t) >= abs(obs) - 1e-12))
    return {"p": (1 + count) / (1 + n_perm), "diff": obs}


def holm(pvals: list) -> list:
    """Holm step-down adjusted p-values (NaNs are left out of the family and stay NaN)."""
    idx = [i for i, p in enumerate(pvals) if p is not None and not math.isnan(p)]
    m = len(idx)
    order = sorted(idx, key=lambda i: (pvals[i], i))
    adj = [float("nan")] * len(pvals)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj
