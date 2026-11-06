#!/usr/bin/env python3
"""Run the log-only monitors (LLM + rule baseline) on finished episodes and score them
against ledger truth. Resumable: an episode whose result file exists (same model, view
and prompt version) is skipped; LLM responses are cached in runs/cache/ as well.

    python scripts/run_monitor.py runs/pilot/r2 --view report --out results/monitor/pilot-report
    python scripts/run_monitor.py runs/grid/A runs/grid/B --view full --out results/monitor/grid-full

Writes <out>/episodes/<label>.json, <out>/episodes.csv, <out>/summary.csv (long format:
monitor, view, group, metric, k, n, rate or value, ci95 lo/hi) and <out>/summary.json.
Aborted episodes (end reason aborted_*) are skipped and listed. Exit 3 when a free quota
stopped the run, exit 4 when an episode's LLM call kept failing (rerun later to resume). See docs/MONITOR.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from monitor.llm_monitor import DEFAULT_BUDGET, MONITOR_MODEL, MONITOR_REV, MONITOR_PROVIDER, PROMPT_VERSION, run_llm_monitor  # noqa: E402
from monitor.score import load_truth, run_baseline, score_episode, summarize  # noqa: E402

EP_FIELDS = ["label", "episode_id", "team_model", "condition", "shape", "seed", "monitor", "view",
             "dup_claims", "dup_truth", "dup_tp", "dup_tp_strict", "dup_attr_ok", "fd_claims", "fd_truth", "fd_tp",
             "agents_claimed", "agents_truth", "agents_tp", "parse_ok", "input_tokens", "output_tokens",
             "prompt_est_tokens", "trimmed"]


def find_episodes(dirs: list) -> list:
    out = []
    for d in dirs:
        d = Path(d)
        for led in sorted(d.rglob("ledger.json")):
            ep = led.parent
            if (ep / "episode.jsonl").exists():
                out.append(ep)
    return sorted(set(out))


def label_of(ep: Path) -> str:
    try:
        rel = ep.resolve().relative_to(ROOT)
    except ValueError:
        rel = ep.resolve()
    return str(rel).strip("/").replace("/", "__")


def team_model(ep: Path) -> str:
    try:
        with open(ep / "manifest.json", encoding="utf-8") as f:
            return str(json.load(f).get("model") or "")
    except (OSError, ValueError):
        return ""


def ep_row(label, truth, tm, mon, view, sc, res) -> dict:
    u = (res or {}).get("usage") or {}
    trim = (res or {}).get("trim") or {}
    return {"label": label, "episode_id": truth["episode_id"], "team_model": tm, "condition": truth["condition"],
            "shape": truth["shape"], "seed": truth["seed"], "monitor": mon, "view": view,
            "dup_claims": sc["dup"]["claims"], "dup_truth": sc["dup"]["truth"], "dup_tp": sc["dup"]["tp"],
            "dup_tp_strict": sc["dup"]["tp_strict"], "dup_attr_ok": sc["dup"]["attr_ok"],
            "fd_claims": sc["fd"]["claims"], "fd_truth": sc["fd"]["truth"], "fd_tp": sc["fd"]["tp"],
            "agents_claimed": " ".join(sc["agents"]["claimed"]), "agents_truth": " ".join(sc["agents"]["truth"]),
            "agents_tp": sc["agents"]["tp"], "parse_ok": sc["parse_ok"],
            "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
            "prompt_est_tokens": (res or {}).get("prompt_est_tokens"),
            "trimmed": bool(trim.get("dropped") or trim.get("shortened") or trim.get("middle_cut"))}


def write_outputs(out: Path, records: list, view: str, skipped: list, calls: dict, budget: int,
                  errors: list = ()) -> dict:
    rows, by = [], defaultdict(list)
    for r in records:
        for mon in ("llm", "baseline"):
            if mon not in r:
                continue
            sc = r[mon]["scores"]
            rows.append(ep_row(r["label"], r["truth"], r["team_model"], mon, view, sc, r[mon].get("result")))
            by[(mon, "all")].append(sc)
            by[(mon, f"team_model={r['team_model']}")].append(sc)
            by[(mon, f"condition={r['truth']['condition']}")].append(sc)
            by[(mon, f"shape={r['truth']['shape']}")].append(sc)
    with open(out / "episodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EP_FIELDS)
        w.writeheader()
        w.writerows(rows)
    summary, srows = {}, []
    for (mon, grp), scs in sorted(by.items()):
        s = summarize(scs)
        summary.setdefault(mon, {})[grp] = s
        for metric, v in s.items():
            if not isinstance(v, dict):
                srows.append({"monitor": mon, "view": view, "group": grp, "metric": metric, "k": v, "n": "",
                              "value": "", "ci95_lo": "", "ci95_hi": "", "ci_kind": ""})
            elif "k" in v:
                srows.append({"monitor": mon, "view": view, "group": grp, "metric": metric, "k": v["k"], "n": v["n"],
                              "value": round(v["rate"], 4) if v["n"] else "", "ci95_lo": round(v["ci95"][0], 4) if v["n"] else "",
                              "ci95_hi": round(v["ci95"][1], 4) if v["n"] else "", "ci_kind": "wilson"})
            else:
                ok = v["value"] == v["value"]
                srows.append({"monitor": mon, "view": view, "group": grp, "metric": metric, "k": "", "n": len(scs),
                              "value": round(v["value"], 4) if ok else "",
                              "ci95_lo": round(v["boot95"][0], 4) if v["boot95"][0] == v["boot95"][0] else "",
                              "ci95_hi": round(v["boot95"][1], 4) if v["boot95"][1] == v["boot95"][1] else "",
                              "ci_kind": "episode_bootstrap"})
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["monitor", "view", "group", "metric", "k", "n", "value", "ci95_lo",
                                          "ci95_hi", "ci_kind"])
        w.writeheader()
        w.writerows(srows)
    meta = {"view": view, "episodes": len(records), "skipped": skipped, "llm_calls": calls,
            "monitor_model": MONITOR_MODEL, "prompt_version": PROMPT_VERSION, "budget_tokens": budget,
            "llm_errors": list(errors)}
    with open(out / "summary.json", "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "summary": summary}, f, indent=1, sort_keys=True)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="run directories (searched recursively for episodes)")
    ap.add_argument("--view", choices=("report", "full"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--monitors", default="llm,baseline", help="comma list of llm, baseline")
    ap.add_argument("--model", default=MONITOR_MODEL)
    ap.add_argument("--provider", default=MONITOR_PROVIDER, help=argparse.SUPPRESS)   # "fake" in tests
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="view budget in estimated tokens")
    ap.add_argument("--limit", type=int, default=0, help="at most N episodes (0 = all)")
    ap.add_argument("--max-calls", type=int, default=0, help="stop after N new LLM calls (0 = no cap)")
    ap.add_argument("--replay-only", action="store_true", help="LLM answers from the cache only; never the network")
    ap.add_argument("--cache-dir", default=str(ROOT / "runs/cache"))
    ap.add_argument("--quota-dir", default=str(ROOT / "runs/cache/_quota"))
    ap.add_argument("--usage-log", default=str(ROOT / "runs/usage/monitor_calls.jsonl"))
    a = ap.parse_args(argv)
    mons = [m.strip() for m in a.monitors.split(",") if m.strip()]
    out = Path(a.out)
    (out / "episodes").mkdir(parents=True, exist_ok=True)

    client = None
    if "llm" in mons:
        from dst.llm import QuotaExhausted, get_client  # noqa: F401
        if a.provider == "fake":
            client = get_client("fake", responder=os.environ.get("MONITOR_FAKE_RESPONSE", "{}"))
        else:
            client = get_client(a.provider, cache_dir=a.cache_dir, quota_dir=a.quota_dir,
                                usage_log=a.usage_log, replay_only=a.replay_only)

    eps = find_episodes(a.runs)
    if a.limit:
        eps = eps[: a.limit]
    records, skipped, errors, quota_stop = [], [], [], False
    calls = {"new": 0, "cached": 0, "errors": 0, "input_tokens": 0, "output_tokens": 0}
    for ep in eps:
        label = label_of(ep)
        truth = load_truth(ep)
        if truth["aborted"]:
            skipped.append({"label": label, "reason": truth["end_reason"]})
            continue
        path = out / "episodes" / f"{label}.json"
        rec = None
        if path.exists():
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            lr = (rec.get("llm") or {}).get("result") or {}
            if "llm" in mons and (lr.get("model") != a.model or lr.get("prompt_version") != PROMPT_VERSION
                                     or lr.get("budget_tokens") != a.budget or lr.get("monitor_rev") != MONITOR_REV):
                rec = None
        if rec is None:
            rec = {"label": label, "episode_dir": str(ep), "team_model": team_model(ep), "truth": truth, "view": a.view}
            if "baseline" in mons:
                b = run_baseline(ep, a.view)
                rec["baseline"] = {"claims": b, "scores": score_episode(b, truth)}
            if "llm" in mons:
                if a.max_calls and calls["new"] >= a.max_calls:
                    print(f"max-calls {a.max_calls} reached; stopping (rerun to resume)", file=sys.stderr)
                    break
                before = client.usage.completions
                try:
                    res = run_llm_monitor(ep, a.view, client, model=a.model, budget_tokens=a.budget)
                except Exception as e:  # noqa: BLE001
                    if getattr(e, "abort_reason", None) == "aborted_quota":
                        print(f"quota: {e}", file=sys.stderr)
                        quota_stop = True
                        break
                    calls["errors"] += 1
                    errors.append({"label": label, "error": f"{type(e).__name__}: {str(e)[:120]}"})
                    print(f"{label}: LLM error {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
                    continue
                delta = client.usage.completions - before
                if delta:
                    calls["new"] += delta
                else:
                    calls["cached"] += 1
                u = res.get("usage") or {}
                calls["input_tokens"] += u.get("input_tokens") or 0
                calls["output_tokens"] += u.get("output_tokens") or 0
                claims = {k: res[k] for k in ("duplicates", "false_dones", "parse_ok", "salvaged")}
                rec["llm"] = {"claims": claims, "scores": score_episode(claims, truth),
                              "result": {k: v for k, v in res.items() if k not in ("duplicates", "false_dones")}}
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f, indent=1, sort_keys=True)
            os.replace(tmp, path)
            print(f"{label}: done", file=sys.stderr)
        records.append(rec)
    summary = write_outputs(out, records, a.view, skipped, calls, a.budget, errors)
    for mon, groups in summary.items():
        s = groups.get("all")
        if not s:
            continue
        def fmt(m):
            v = s[m]
            return f"{v['k']}/{v['n']}" if "k" in v else f"{v['value']:.2f}"
        print(f"{mon:8s} view={a.view} n={s['episodes']}  dup P={fmt('dup_precision')} R={fmt('dup_recall')} "
              f"attr={fmt('dup_attr_acc')}  fd P={fmt('fd_precision')} R={fmt('fd_recall')}  "
              f"agents P={fmt('agent_precision')} R={fmt('agent_recall')}  parse_fail={s['parse_failures']}")
    print(json.dumps({"llm_calls": calls, "skipped": len(skipped), "llm_errors": [e["label"] for e in errors]}))
    if errors:
        print(f"{len(errors)} episode(s) failed after retries and are not in the summary; rerun to resume",
              file=sys.stderr)
    return 3 if quota_stop else (4 if errors else 0)


if __name__ == "__main__":
    sys.exit(main())
