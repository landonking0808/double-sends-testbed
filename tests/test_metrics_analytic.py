"""Validate the metric code against closed-form expectations for the scripted policies.

Tolerance. For each check the observed quantity is a total over episodes of independent
per-customer (or per-report) contributions whose exact mean and variance we compute by
enumeration (``dst.analytic``). We require |observed - expected| <= 4 standard deviations
of that total. Under the analytic model a 4-sigma miss has probability about 6e-5
(normal approximation; sums of a few hundred bounded terms), so a correct metric
practically never fails; the seed list is fixed, so the test is deterministic and cannot
flake. A tolerance is only meaningful if it can fail: ``test_checks_have_power`` shows
that models which ignore a single mechanism (retries after timeout, or bounces) miss by
far more than 4 sigma on the same episodes.
"""
import functools
import math

import pytest

from dst.analytic import naive_episode_extras, naive_node_false_prob
from dst.env import FaultConfig
from dst.metrics import _node_customers
from dst.run import run_episode

FAULTS = FaultConfig()  # p_timeout 0.2, p_bounce 0.15, stale 0.5: the defaults the experiments use
R = 3                   # naive retrier: at most 3 retries after a timeout
SEEDS = range(1, 201)   # 200 episodes per cell, about 10-20 ms each


def _episodes(shape, condition, agents, n_repeat=3, seeds=SEEDS, faults=FAULTS):
    return _cached(shape, condition, agents, n_repeat, tuple(seeds), faults)


@functools.lru_cache(maxsize=None)
def _cached(shape, condition, agents, n_repeat, seeds, faults):
    return [run_episode(shape=shape, condition=condition, agents=agents, seed=s, faults=faults, n_repeat=n_repeat,
                        max_retries=R)[1:] for s in seeds]


def _z(obs, mean, var):
    return (obs - mean) / math.sqrt(var) if var > 0 else (0.0 if obs == mean else math.inf)


def _dar_check(eps, p, b):
    obs = sum(m["dar"]["extras"] for _, m in eps)
    mean = var = 0.0
    for led, _ in eps:
        mu, v = naive_episode_extras(led["batch"], p, b, R)
        mean += mu
        var += v
    req = sum(m["dar"]["required"] for _, m in eps)
    return obs, mean, var, req


@pytest.mark.parametrize("shape,condition", [("F", "B0"), ("D", "P1"), ("F", "E2")])
def test_naive_dar_matches_analytic(shape, condition):
    # P1 changes only prompts (scripted agents ignore them) and E2 gates reports, not effects,
    # so the naive DAR expectation is the same in all three cells.
    eps = _episodes(shape, condition, "scripted-naive")
    obs, mean, var, req = _dar_check(eps, FAULTS.p_timeout, FAULTS.p_bounce)
    z = _z(obs, mean, var)
    print(f"{shape}/{condition} naive DAR per 100: measured {100 * obs / req:.2f} vs analytic {100 * mean / req:.2f} (z={z:+.2f})")
    assert abs(z) <= 4


def test_naive_worker_fcr_matches_analytic():
    # No repeat customers: each customer belongs to exactly one worker, so a worker's done is
    # false iff some customer of its had every send bounce: 1 - prod(1 - E[b^N]).
    eps = _episodes("F", "B0", "scripted-naive", n_repeat=0)
    obs = n = 0
    mean = var = 0.0
    for led, m in eps:
        for rep in led["reports"]:
            if rep["accepted"] and rep["status"] == "done" and rep["agent"].startswith("w"):
                q = naive_node_false_prob(led["batch"], _node_customers(led, rep["agent"]), FAULTS.p_timeout,
                                          FAULTS.p_bounce, R)
                mean += q
                var += q * (1 - q)
                n += 1
        obs += m["fcr"]["worker"][0]
    z = _z(obs, mean, var)
    print(f"naive worker FCR: measured {obs / n:.4f} vs analytic {mean / n:.4f} over {n} dones (z={z:+.2f})")
    assert abs(z) <= 4


def test_naive_lead_fcr_and_pcr():
    # B0: the naive lead reports done right after delegating: always premature, always false.
    for shape in ("F", "D"):
        eps = _episodes(shape, "B0", "scripted-naive", seeds=range(1, 21))
        assert all(m["pcr"]["lead"] == [1, 1] and m["fcr"]["lead"] == [1, 1] for _, m in eps)
    # E2: the lead's done is accepted only once no descendant runs, i.e. after every effect;
    # it is false iff some customer's every send bounced: 1 - prod_c(1 - E[b^N]^m_c).
    eps = _episodes("F", "E2", "scripted-naive")
    assert all(m["pcr"]["lead"] == [0, 1] for _, m in eps)
    obs = sum(m["fcr"]["lead"][0] for _, m in eps)
    qs = [naive_node_false_prob(led["batch"], sorted(led["batch"]["customers"]), FAULTS.p_timeout, FAULTS.p_bounce, R)
          for led, _ in eps]
    z = _z(obs, sum(qs), sum(q * (1 - q) for q in qs))
    print(f"naive lead FCR under E2: measured {obs / len(eps):.4f} vs analytic {sum(qs) / len(eps):.4f} (z={z:+.2f})")
    assert abs(z) <= 4
    # and the gate makes every accepted worker done true
    assert sum(m["fcr"]["worker"][0] for _, m in eps) == 0


@pytest.mark.parametrize("condition", ["E1", "E3"])
def test_idempotency_removes_all_duplicates(condition):
    for shape in ("F", "D"):
        eps = _episodes(shape, condition, "scripted-naive", seeds=range(1, 31))
        assert sum(m["dar"]["extras"] for _, m in eps) == 0
        assert sum(m["cost"]["suppressed"] + m["cost"]["blocked"] for _, m in eps) > 0


@pytest.mark.parametrize("condition", ["B0", "P1", "E1", "E2", "E3"])
def test_careful_policy_is_the_ceiling(condition):
    # The careful policy's claim protocol tolerates one tick of board staleness and it reads
    # back after every ambiguous result, so its expected DAR and FCR are exactly 0.
    for shape in ("F", "D"):
        eps = _episodes(shape, condition, "scripted-careful", seeds=range(1, 16))
        assert sum(m["dar"]["extras"] for _, m in eps) == 0
        assert sum(m["fcr"]["all"][0] for _, m in eps) == 0
        assert sum(m["pcr"]["all"][0] for _, m in eps) == 0
        assert all(m["task_success"]["met"] == m["task_success"]["total"] for _, m in eps)


def test_fault_free_naive_duplicates_are_exactly_the_overlaps():
    eps = _episodes("F", "B0", "scripted-naive", seeds=range(1, 21), faults=FaultConfig.zero())
    for led, m in eps:
        mean, var = naive_episode_extras(led["batch"], 0.0, 0.0, R)
        assert var == 0 and m["dar"]["extras"] == mean  # no faults: duplicates come only from repeat customers


def test_checks_have_power():
    eps = _episodes("F", "B0", "scripted-naive")
    obs, mean, var, _ = _dar_check(eps, FAULTS.p_timeout, FAULTS.p_bounce)
    obs2, mean_no_retry, var_no_retry, _ = _dar_check(eps, 0.0, FAULTS.p_bounce)   # ignores timeouts
    obs3, mean_no_bounce, var_no_bounce, _ = _dar_check(eps, FAULTS.p_timeout, 0.0)  # ignores bounces
    assert abs(_z(obs, mean, var)) <= 4
    assert abs(_z(obs, mean_no_retry, max(var_no_retry, 1e-9))) > 10
    assert abs(_z(obs, mean_no_bounce, var_no_bounce)) > 4
