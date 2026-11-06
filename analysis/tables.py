"""Pooled tables (Table 1), baseline-vs-control tests and the scripted-agent validation.

Cells are (model, fault profile, shape, condition); ``shape = all`` pools the shapes.

* DAR per 100 is the pooled ratio 100 * sum(extras) / sum(required) with a seed-level
  (cluster) bootstrap 95% interval: seeds are resampled with replacement, all of a seed's
  episodes in the cell move together.
* Shares (episodes with >= 1 duplicate, FCR, PCR, task success, report fidelity) are pooled
  counts with 95% Wilson intervals.
* Cost: mean steps, LLM calls and tokens per episode; overhead of a control is
  mean(control) / mean(B0) - 1 in the same (model, faults, shape).
* Tests (B0 vs each control): two-sided Fisher exact tests on episode-level binary outcomes
  and a two-sided permutation test (difference in mean per-episode DAR per 100, labels
  permuted within shape when shapes are pooled). Holm correction across the controls,
  separately for every (model, faults, shape, outcome).
"""
from __future__ import annotations

import math
from collections import defaultdict

from .stats import N_BOOT, N_PERM, cluster_bootstrap_ratio, fisher_exact, holm, permutation_test, rate, sub_seed

CONDITION_ORDER = ["B0", "P1", "E1", "E2", "E3"]
SHAPE_ORDER = ["F", "D", "D+"]
BASELINE = "B0"


def _order(seq, ref):
    return sorted(set(seq), key=lambda x: (ref.index(x) if x in ref else len(ref), str(x)))


def cells(rows: list) -> dict:
    """Group included rows: (model, faults, shape, condition) -> rows; plus shape 'all'."""
    out = defaultdict(list)
    for r in rows:
        if not r["included"]:
            continue
        out[(r["model"], r["faults"], r["shape"], r["condition"])].append(r)
        out[(r["model"], r["faults"], "all", r["condition"])].append(r)
    return out


def sort_key(key):
    model, faults, shape, cond = key
    s = SHAPE_ORDER.index(shape) if shape in SHAPE_ORDER else (len(SHAPE_ORDER) + (shape == "all"))
    c = CONDITION_ORDER.index(cond) if cond in CONDITION_ORDER else len(CONDITION_ORDER)
    return (str(model), str(faults), s, str(shape), c, str(cond))


def _pool(rs, k, n):
    return rate(sum(r[k] for r in rs), sum(r[n] for r in rs))


def _mean(rs, k):
    return sum(r[k] for r in rs) / len(rs) if rs else float("nan")


def _put(d, prefix, rt):
    d[f"{prefix}_k"], d[f"{prefix}_n"], d[prefix], d[f"{prefix}_lo"], d[f"{prefix}_hi"] = (
        rt["k"], rt["n"], rt["rate"], rt["lo"], rt["hi"])


def table1(rows: list, boot_seed: int = 0, n_boot: int = N_BOOT) -> list:
    groups = cells(rows)
    excluded = defaultdict(int)
    for r in rows:
        if not r["included"]:
            excluded[(r["model"], r["faults"], r["shape"], r["condition"])] += 1
            excluded[(r["model"], r["faults"], "all", r["condition"])] += 1
    out = []
    for key in sorted(groups, key=sort_key):
        rs = groups[key]
        model, faults, shape, cond = key
        num, den = defaultdict(float), defaultdict(float)
        for r in rs:
            num[r["seed"]] += r["dar_extras"]
            den[r["seed"]] += r["dar_required"]
        req = sum(den.values())
        dar = 100.0 * sum(num.values()) / req if req else float("nan")
        lo, hi = cluster_bootstrap_ratio(num, den, n_boot=n_boot, seed=sub_seed(boot_seed, *key), scale=100.0)
        per_ep = [r["dar_per100"] for r in rs]
        mu = sum(per_ep) / len(per_ep)
        sd = math.sqrt(sum((x - mu) ** 2 for x in per_ep) / (len(per_ep) - 1)) if len(per_ep) > 1 else 0.0
        d = {"model": model, "faults": faults, "shape": shape, "condition": cond, "n_episodes": len(rs),
             "n_seeds": len(num), "n_excluded": excluded.get(key, 0), "n_max_ticks": sum(r["hit_max_ticks"] for r in rs),
             "n_call_cap": sum(r["hit_call_cap"] for r in rs),
             "n_client_error_episodes": sum(1 for r in rs if r["client_errors"]),
             "dar_per100": dar, "dar_per100_lo": lo, "dar_per100_hi": hi, "dar_per100_episode_mean": mu,
             "dar_per100_episode_sd": sd}
        _put(d, "dup_episodes", rate(sum(r["any_duplicate"] for r in rs), len(rs)))
        for role in ("worker", "manager", "lead", "all"):
            _put(d, f"fcr_{role}", _pool(rs, f"fcr_{role}_k", f"fcr_{role}_n"))
        _put(d, "pcr_all", _pool(rs, "pcr_all_k", "pcr_all_n"))
        _put(d, "pcr_lead", _pool(rs, "pcr_lead_k", "pcr_lead_n"))
        _put(d, "task_success", _pool(rs, "task_met", "task_total"))
        _put(d, "task_complete_episodes", rate(sum(r["task_complete"] for r in rs), len(rs)))
        _put(d, "fidelity_precision", _pool(rs, "fid_claimed_true", "fid_claimed"))
        _put(d, "fidelity_recall", _pool(rs, "fid_met_claimed", "fid_met"))
        _put(d, "failure_episodes", rate(sum(r["any_failure"] for r in rs), len(rs)))
        for k in ("steps", "llm_calls", "tokens", "input_tokens", "output_tokens", "parse_failures", "suppressed",
                  "blocked", "refused_reports"):
            d[f"mean_{k}"] = _mean(rs, k)
        out.append(d)
    # overhead and reduction vs B0 in the same (model, faults, shape)
    base = {(d["model"], d["faults"], d["shape"]): d for d in out if d["condition"] == BASELINE}
    for d in out:
        b = base.get((d["model"], d["faults"], d["shape"]))
        for k in ("steps", "llm_calls", "tokens"):
            bv = b[f"mean_{k}"] if b else float("nan")
            d[f"{k}_overhead_vs_b0"] = (d[f"mean_{k}"] / bv - 1.0) if b and bv else float("nan")
        # the cost measure used in Figure 2: tokens if the team used a model, else scheduler steps
        use_tokens = b is not None and b["mean_tokens"] > 0
        d["cost_measure"] = "tokens" if use_tokens else "steps"
        d["cost_overhead_vs_b0"] = d["tokens_overhead_vs_b0"] if use_tokens else d["steps_overhead_vs_b0"]
        for k in ("dar_per100", "failure_episodes", "fcr_all", "dup_episodes"):
            d[f"{k}_reduction_vs_b0"] = (b[k] - d[k]) if b else float("nan")
    return out


TEST_OUTCOMES = [  # (outcome, column, description)
    ("any_duplicate", "any_duplicate", "episode has >= 1 duplicate effect"),
    ("any_false_done", "any_false_done", "episode has >= 1 false done report"),
    ("any_premature", "any_premature", "episode has >= 1 premature done report"),
    ("task_incomplete", "task_complete", "episode ends with an unmet requirement"),
]


def tests(rows: list, perm_seed: int = 0, n_perm: int = N_PERM) -> list:
    groups = cells(rows)
    fams = defaultdict(list)
    for (model, faults, shape, cond), rs in groups.items():
        if cond == BASELINE:
            continue
        b = groups.get((model, faults, shape, BASELINE))
        if not b:
            continue
        fam = (model, faults, shape)
        for name, col, desc in TEST_OUTCOMES:
            flip = name == "task_incomplete"
            kb = sum((1 - r[col]) if flip else r[col] for r in b)
            kc = sum((1 - r[col]) if flip else r[col] for r in rs)
            fe = fisher_exact(kb, len(b), kc, len(rs))
            fams[fam + (name,)].append({
                "model": model, "faults": faults, "shape": shape, "outcome": name, "test": "fisher_exact",
                "control": cond, "n_b0": len(b), "n_control": len(rs), "b0_value": kb / len(b),
                "control_value": kc / len(rs), "effect": kc / len(rs) - kb / len(b), "effect_kind": "risk_difference",
                "odds_ratio": fe["odds_ratio"], "p": fe["p"], "description": desc})
        pt = permutation_test([r["dar_per100"] for r in b], [r["dar_per100"] for r in rs],
                              [r["shape"] for r in b], [r["shape"] for r in rs], n_perm=n_perm,
                              seed=sub_seed(perm_seed, model, faults, shape, cond))
        fams[fam + ("dar_per100",)].append({
            "model": model, "faults": faults, "shape": shape, "outcome": "dar_per100", "test": "permutation",
            "control": cond, "n_b0": len(b), "n_control": len(rs), "b0_value": _mean(b, "dar_per100"),
            "control_value": _mean(rs, "dar_per100"), "effect": pt["diff"], "effect_kind": "mean_difference",
            "odds_ratio": float("nan"), "p": pt["p"],
            "description": f"per-episode DAR per 100, {n_perm} permutations" + (" within shape" if shape == "all" else "")})
    out = []
    for fam in sorted(fams, key=lambda f: sort_key(f[:3] + ("",)) + (f[3],)):
        lst = sorted(fams[fam], key=lambda d: sort_key((d["model"], d["faults"], d["shape"], d["control"])))
        adj = holm([d["p"] for d in lst])
        for d, a in zip(lst, adj):
            d["p_holm"] = a
            d["holm_family_size"] = sum(1 for x in lst if not math.isnan(x["p"]))
            d["reject_holm_0.05"] = int(not math.isnan(a) and a < 0.05)
            out.append(d)
    return out


# --------------------------------------------------------------------------------------
# scripted-agent validation: measured metrics vs closed-form expectations (dst.analytic)

def validation(rows: list, metrics: dict, ledgers: dict, boot_seed: int = 0, n_boot: int = N_BOOT) -> list:
    """Rows of measured vs analytic DAR (and lead FCR) for scripted teams.

    Expectations: naive retrier without idempotency (B0, P1, E2): ``naive_episode_extras``;
    with idempotency (E1, E3): 0 duplicates. Careful policy: 0 duplicates and 0 false dones
    everywhere. Naive lead FCR: 1 in B0 and P1 (it reports right after delegating); in E2
    ``naive_node_false_prob`` over all customers.
    """
    from dst.analytic import naive_episode_extras, naive_node_false_prob
    grp = defaultdict(list)
    for r in rows:
        if r["included"] and r["agents"] in ("scripted-naive", "scripted-careful"):
            grp[(r["agents"], r["faults"], r["shape"], r["condition"])].append(r)
    out = []
    for key in sorted(grp, key=sort_key):
        agents, faults, shape, cond = key
        rs = grp[key]
        obs = sum(r["dar_extras"] for r in rs)
        req = sum(r["dar_required"] for r in rs)
        num = defaultdict(float)
        den = defaultdict(float)
        for r in rs:
            num[r["seed"]] += r["dar_extras"]
            den[r["seed"]] += r["dar_required"]
        lo, hi = cluster_bootstrap_ratio(num, den, n_boot=n_boot, seed=sub_seed(boot_seed, "val", *key), scale=100.0)
        mean = var = 0.0
        fl_obs = fl_n = 0
        fl_mean = fl_var = 0.0
        lead_model = None
        for r in rs:
            led = ledgers[r["run_dir"]]
            fa = led["config"]["faults"]
            p, b, R = fa["p_timeout"], fa["p_bounce"], int(r["max_retries"] or 3)
            if agents == "scripted-naive" and cond in ("B0", "P1", "E2"):
                mu, v = naive_episode_extras(led["batch"], p, b, R)
                mean += mu
                var += v
            fl_obs += r["fcr_lead_k"]
            fl_n += r["fcr_lead_n"]
            if agents == "scripted-careful":
                lead_model = "zero"
            elif cond in ("B0", "P1"):
                lead_model = "always_false"
                fl_mean += r["fcr_lead_n"]
            elif cond == "E2":
                lead_model = "readback_gate"
                if r["fcr_lead_n"]:
                    q = naive_node_false_prob(led["batch"], sorted(led["batch"]["customers"]), p, b, R)
                    fl_mean += q
                    fl_var += q * (1 - q)
        sd = math.sqrt(var)
        z = (obs - mean) / sd if sd > 0 else (0.0 if obs == mean else float("inf"))
        d = {"agents": agents, "faults": faults, "shape": shape, "condition": cond, "n_episodes": len(rs),
             "dar_measured_per100": 100.0 * obs / req if req else float("nan"), "dar_measured_lo": lo,
             "dar_measured_hi": hi, "dar_analytic_per100": 100.0 * mean / req if req else float("nan"),
             "dar_analytic_sd_per100": 100.0 * sd / req if req else float("nan"), "dar_z": z,
             "lead_fcr_model": lead_model or "", "lead_dones": fl_n}
        if lead_model and fl_n:
            lr = rate(fl_obs, fl_n)
            fz_sd = math.sqrt(fl_var)
            d.update({"lead_fcr_measured": lr["rate"], "lead_fcr_lo": lr["lo"], "lead_fcr_hi": lr["hi"],
                      "lead_fcr_analytic": fl_mean / fl_n,
                      "lead_fcr_z": (fl_obs - fl_mean) / fz_sd if fz_sd > 0 else (0.0 if fl_obs == fl_mean else float("inf"))})
        else:
            d.update({"lead_fcr_measured": float("nan"), "lead_fcr_lo": float("nan"), "lead_fcr_hi": float("nan"),
                      "lead_fcr_analytic": float("nan"), "lead_fcr_z": float("nan")})
        out.append(d)
    return out
