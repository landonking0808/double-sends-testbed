"""Closed-form expectations for the scripted naive retrier, used to validate the metric code.

Naive worker, per assigned message, per intent: it calls the tool, and while the result is
``timeout`` it calls again, at most ``R`` retries. Each call independently returns
``timeout`` with probability ``p`` (the effect is applied anyway), so the number of applied
calls ``N`` has

    P(N = k) = p^(k-1) (1 - p)   for k = 1..R,     P(N = R + 1) = p^R.

A send additionally bounces independently with probability ``b``. A customer who wrote
``m`` times is processed ``m`` times (by one or several workers), so per (customer, intent)
the total applied count is ``T = N_1 + ... + N_m``; for records and refunds the extras are
``T - 1``; for replies only delivered copies count: ``D ~ Binomial(T, 1 - b)``, extras
``max(D - 1, 0)``. A customer's reply requirement fails iff every send bounced:
``P = E[b^N]^m``. All draws are independent, so means and variances add.
"""
from __future__ import annotations

from math import comb


def attempts_dist(p: float, max_retries: int) -> dict:
    d = {k: p ** (k - 1) * (1 - p) for k in range(1, max_retries + 1)}
    d[max_retries + 1] = p ** max_retries
    return d


def convolve(a: dict, b: dict) -> dict:
    out: dict = {}
    for i, pi in a.items():
        for j, pj in b.items():
            out[i + j] = out.get(i + j, 0.0) + pi * pj
    return out


def total_dist(p: float, max_retries: int, m: int) -> dict:
    base = attempts_dist(p, max_retries)
    d = base
    for _ in range(m - 1):
        d = convolve(d, base)
    return d


def _moments(dist: dict) -> tuple:
    mean = sum(x * q for x, q in dist.items())
    var = sum((x - mean) ** 2 * q for x, q in dist.items())
    return mean, var


def extras_applied(p: float, max_retries: int, m: int) -> tuple:
    """(mean, var) of extra applied effects T - 1 (refund or record intent)."""
    return _moments({t - 1: q for t, q in total_dist(p, max_retries, m).items()})


def extras_reply(p: float, b: float, max_retries: int, m: int) -> tuple:
    """(mean, var) of extra *delivered* replies max(D - 1, 0)."""
    dist: dict = {}
    for t, q in total_dist(p, max_retries, m).items():
        for d in range(t + 1):
            pd = comb(t, d) * (1 - b) ** d * b ** (t - d)
            x = max(d - 1, 0)
            dist[x] = dist.get(x, 0.0) + q * pd
    return _moments(dist)


def reply_fail_prob(p: float, b: float, max_retries: int, m: int = 1) -> float:
    """P(every send for a customer bounced) = E[b^N]^m."""
    e = sum(q * b ** k for k, q in attempts_dist(p, max_retries).items())
    return e ** m


def message_counts(batch_dict: dict) -> dict:
    counts: dict = {}
    for msg in batch_dict["messages"]:
        counts[msg["customer"]] = counts.get(msg["customer"], 0) + 1
    return counts


def _eligible(order: dict, policy: dict) -> bool:
    return (order["issue"] in policy["eligible_issues"] and int(order["days_since_order"]) <= int(policy["max_days"])
            and int(order["amount"]) <= int(policy["max_amount"]))


def naive_episode_extras(batch_dict: dict, p: float, b: float, max_retries: int) -> tuple:
    """(mean, var) of the number of duplicate effects in one naive episode (no locks)."""
    mean = var = 0.0
    pol = batch_dict["policy"]
    for c, m in message_counts(batch_dict).items():
        parts = [extras_reply(p, b, max_retries, m), extras_applied(p, max_retries, m)]
        if _eligible(batch_dict["customers"][c], pol):
            parts.append(extras_applied(p, max_retries, m))
        for mu, v in parts:
            mean += mu
            var += v
    return mean, var


def naive_node_false_prob(batch_dict: dict, customers: list, p: float, b: float, max_retries: int) -> float:
    """P(a node's done is false) when every message of its customers was fully processed
    before the report: 1 - prod_c (1 - E[b^N]^m_c)."""
    counts = message_counts(batch_dict)
    ok = 1.0
    for c in customers:
        ok *= 1 - reply_fail_prob(p, b, max_retries, counts[c])
    return 1 - ok
