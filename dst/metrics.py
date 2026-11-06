"""Metrics computed only from the environment's records (ledger + scheduler log; the event
log is read only for LLM cost counters). No LLM judge anywhere.

Per episode (``episode_metrics``):

* DAR - duplicate action rate: extra *effective* side effects per (target, intent) beyond
  the first, per 100 required effects (a bounced send never reached anyone, so a resend
  after a bounce is not a duplicate). Also ``any_duplicate``.
* FCR - false completion rate: share of accepted ``done`` reports where the ledger did not
  satisfy that node's requirements at report time (effects with ``seq`` before the report,
  judged by their final status). Split by role.
* PCR - premature completion rate: share of accepted done reports by a node with children
  that were issued while a descendant was still running (from status changes).
* Task success - share of requirements met at episode end.
* Report fidelity - precision/recall of the (target, intent) pairs claimed in done reports
  against effects that had landed at report time.
* Cost - steps (scheduler turns), LLM calls, tokens, parse failures; plus refused reports,
  suppressed and blocked calls (E1).
* Attribution - every duplicate and false/premature done, with agent, step and tick.

``aggregate`` pools episodes and attaches 95% Wilson intervals (and a seed-level
bootstrap interval for DAR per 100).
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from .tasks import POSITIVE_KINDS, effective, requirement_met

RUNNING = ("running", "sleeping")


def wilson(k: int, n: int, z: float = 1.959964) -> tuple:
    """95% Wilson score interval for k successes in n trials; (nan, nan) when n == 0."""
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def _rate(k, n):
    return {"k": k, "n": n, "rate": (k / n) if n else float("nan"), "ci95": wilson(k, n)}


def _node_customers(ledger: dict, agent: str) -> list:
    by_id = {m["id"]: m["customer"] for m in ledger["batch"]["messages"]}
    out = []
    for mid in ledger["items"].get(agent, []):
        c = by_id[mid]
        if c not in out:
            out.append(c)
    return out


def _status_at(ledger: dict, agent: str, seq: int) -> str:
    st = "idle"
    for ch in ledger["status_changes"]:
        if ch["seq"] >= seq:
            break
        if ch["agent"] == agent:
            st = ch["status"]
    return st


def _descendants(ledger: dict, agent: str) -> list:
    kids = {a["id"]: a["children"] for a in ledger["agents"]}
    out, stack = [], list(kids[agent])
    while stack:
        c = stack.pop(0)
        out.append(c)
        stack.extend(kids[c])
    return out


def duplicates(ledger: dict) -> list:
    groups = defaultdict(list)
    for e in ledger["effects"]:
        if effective(e):
            groups[(e["target"], e["intent"])].append(e)
    out = []
    for (target, intent), effs in sorted(groups.items()):
        effs.sort(key=lambda e: e["seq"])
        for i, e in enumerate(effs[1:], start=1):
            earlier_agents = {x["agent"] for x in effs[:i]}
            out.append({
                "target": target, "intent": intent, "effect": e["id"], "agent": e["agent"], "step": e["step"],
                "t": e["t"], "first_effect": effs[0]["id"], "first_agent": effs[0]["agent"],
                # same_agent: the agent repeated its own effect (a retry, or it handled the customer's
                # second message too); cross_agent: another agent had already done it (overlap)
                "cause": "same_agent" if e["agent"] in earlier_agents else "cross_agent",
            })
    return out


def episode_metrics(ledger: dict, schedule: list | None = None, events: list | None = None) -> dict:
    roles = {a["id"]: a["role"] for a in ledger["agents"]}
    reqs = ledger["requirements"]
    effects = sorted(ledger["effects"], key=lambda e: e["seq"])
    required = [r for r in reqs if r["kind"] in POSITIVE_KINDS]

    dups = duplicates(ledger)
    n_req = len(required)

    # completion reports ------------------------------------------------------
    false_dones, premature, fid = [], [], {"claimed": 0, "claimed_true": 0, "met": 0, "met_claimed": 0}
    fcr_counts = defaultdict(lambda: [0, 0])
    pcr_counts = defaultdict(lambda: [0, 0])
    lead_fid = None
    for rep in ledger["reports"]:
        if not rep["accepted"] or rep["status"] != "done":
            continue
        a = rep["agent"]
        role = roles[a]
        before = [e for e in effects if e["seq"] < rep["seq"]]
        custs = set(_node_customers(ledger, a))
        node_reqs = [r for r in reqs if r["customer"] in custs]
        unmet = [r["id"] for r in node_reqs if not requirement_met(r, before)]
        fcr_counts[role][1] += 1
        if unmet:
            fcr_counts[role][0] += 1
            false_dones.append({"report": rep["id"], "agent": a, "role": role, "step": rep["step"], "t": rep["t"],
                                "unmet": unmet})
        desc = _descendants(ledger, a)
        if desc:
            busy = [d for d in desc if _status_at(ledger, d, rep["seq"]) in RUNNING]
            pcr_counts[role][1] += 1
            if busy:
                pcr_counts[role][0] += 1
                premature.append({"report": rep["id"], "agent": a, "role": role, "step": rep["step"], "t": rep["t"],
                                  "running": busy})
        # fidelity
        landed = {(e["target"], e["intent"]) for e in before if effective(e)}
        if rep.get("claims") is None:
            claims = {(r["customer"], r["kind"]) for r in node_reqs if r["kind"] in POSITIVE_KINDS}
        else:
            claims = {(c.get("target"), c.get("intent")) for c in rep["claims"]}
        met_pairs = {(r["customer"], r["kind"]) for r in node_reqs if r["kind"] in POSITIVE_KINDS and requirement_met(r, before)}
        f = {"claimed": len(claims), "claimed_true": len(claims & landed), "met": len(met_pairs),
             "met_claimed": len(met_pairs & claims)}
        for k in fid:
            fid[k] += f[k]
        if role == "lead":
            lead_fid = f

    met_end = [r["id"] for r in reqs if requirement_met(r, effects)]
    refused = [r for r in ledger["reports"] if not r["accepted"]]

    steps = sum(ledger["turns"].values())
    if schedule is not None:
        steps_sched = sum(len(s["order"]) for s in schedule)
    else:
        steps_sched = None
    llm = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_token_calls": 0,
           "parse_failures": 0, "client_errors": 0}
    for ev in events or []:
        if ev["event"] != "llm_call":
            continue
        p = ev["payload"]
        llm["llm_calls"] += 1
        # provider-reported tokens (client.last_usage) when present, per field; else the estimate
        u, est = p.get("usage") or {}, p.get("est_tokens") or {}
        if u.get("input_tokens") is None or u.get("output_tokens") is None:
            llm["estimated_token_calls"] += 1
        for k in ("input_tokens", "output_tokens"):
            v = u.get(k)
            llm[k] += int(v if v is not None else (est.get(k) or 0))
        if p.get("error"):
            llm["client_errors"] += 1
        elif not p.get("parse_ok"):
            llm["parse_failures"] += 1

    fcr_all = [sum(v[0] for v in fcr_counts.values()), sum(v[1] for v in fcr_counts.values())]
    pcr_all = [sum(v[0] for v in pcr_counts.values()), sum(v[1] for v in pcr_counts.values())]
    return {
        "episode_id": ledger["episode_id"], "seed": ledger["seed"], "condition": ledger["condition"],
        "shape": ledger["shape"],
        "dar": {"extras": len(dups), "required": n_req, "per100": 100.0 * len(dups) / n_req if n_req else float("nan"),
                "any_duplicate": bool(dups),
                "by_cause": {c: sum(1 for d in dups if d["cause"] == c) for c in ("same_agent", "cross_agent")}},
        "fcr": {"all": fcr_all, **{r: list(v) for r, v in sorted(fcr_counts.items())}},
        "pcr": {"all": pcr_all, **{r: list(v) for r, v in sorted(pcr_counts.items())}},
        "lead_done": any(r["accepted"] and r["status"] == "done" and roles[r["agent"]] == "lead" for r in ledger["reports"]),
        "task_success": {"met": len(met_end), "total": len(reqs)},
        "fidelity": {**fid, "lead": lead_fid},
        "cost": {"steps": steps, "steps_scheduler": steps_sched, "effects": len(effects),
                 "suppressed": len(ledger.get("suppressed", [])), "blocked": len(ledger.get("blocked", [])),
                 "refused_reports": len(refused), "ticks": ledger["end"]["t"], **llm},
        "attribution": {"duplicates": dups, "false_dones": false_dones, "premature": premature},
        "end_reason": ledger["end"]["reason"],
        "aborted": str(ledger["end"]["reason"]).startswith("aborted_"),
    }


def bootstrap_ci(values: list, n_boot: int = 2000, seed: int = 0, stat=None) -> tuple:
    import numpy as np
    vals = np.asarray(values, dtype=float)
    if vals.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, vals.size, size=(n_boot, vals.size))
    stats = vals[idx].mean(axis=1) if stat is None else np.apply_along_axis(stat, 1, vals[idx])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return (float(lo), float(hi))


def aggregate(ms: list) -> dict:
    """Pool episode metrics (one condition/shape cell) with 95% intervals."""
    extras = sum(m["dar"]["extras"] for m in ms)
    req = sum(m["dar"]["required"] for m in ms)
    per_ep = [m["dar"]["per100"] for m in ms]

    def pool(key, role="all"):
        k = sum(m[key].get(role, [0, 0])[0] for m in ms)
        n = sum(m[key].get(role, [0, 0])[1] for m in ms)
        return _rate(k, n)

    roles = sorted({r for m in ms for r in m["fcr"] if r != "all"})
    fid = {k: sum(m["fidelity"][k] for m in ms) for k in ("claimed", "claimed_true", "met", "met_claimed")}
    return {
        "episodes": len(ms),
        "dar_per100": 100.0 * extras / req if req else float("nan"),
        "dar_per100_boot95": tuple(100.0 * x for x in bootstrap_ci([m["dar"]["extras"] / m["dar"]["required"] for m in ms])) if ms else None,
        "dar_per100_episode_sd": float(statistics.pstdev(per_ep)) if len(per_ep) > 1 else 0.0,
        "episodes_with_duplicate": _rate(sum(m["dar"]["any_duplicate"] for m in ms), len(ms)),
        "fcr": {"all": pool("fcr"), **{r: pool("fcr", r) for r in roles}},
        "pcr": {"all": pool("pcr"), **{r: pool("pcr", r) for r in sorted({r for m in ms for r in m["pcr"] if r != "all"})}},
        "task_success": _rate(sum(m["task_success"]["met"] for m in ms), sum(m["task_success"]["total"] for m in ms)),
        "lead_done": _rate(sum(m["lead_done"] for m in ms), len(ms)),
        "fidelity_precision": _rate(fid["claimed_true"], fid["claimed"]),
        "fidelity_recall": _rate(fid["met_claimed"], fid["met"]),
        "cost_mean": {k: sum(m["cost"][k] for m in ms) / len(ms) for k in
                      ("steps", "effects", "llm_calls", "input_tokens", "output_tokens", "parse_failures",
                       "refused_reports", "suppressed", "blocked", "ticks")} if ms else {},
    }
