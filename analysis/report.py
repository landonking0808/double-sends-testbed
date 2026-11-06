"""Write the analysis outputs: CSV tables, a Markdown Table 1 and a summary JSON.

Output is deterministic: rows are sorted, floats are written with 6 significant digits,
no timestamps. Running the analysis twice on the same episode logs gives identical bytes.
"""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path

from . import tables
from .load import load_rows
from .stats import N_BOOT, N_PERM

EPISODE_BASE = ["run_dir", "episode_id", "included", "excluded_reason", "aborted", "model", "agents", "provider",
                "condition", "shape", "seed", "faults", "n_messages", "n_repeat", "max_retries", "git_commit"]
EPISODE_METRICS = [
    "end_reason", "hit_max_ticks", "hit_call_cap", "dar_extras", "dar_required", "dar_per100", "any_duplicate", "dup_same_agent",
    "dup_cross_agent", "fcr_all_k", "fcr_all_n", "fcr_worker_k", "fcr_worker_n", "fcr_manager_k", "fcr_manager_n",
    "fcr_lead_k", "fcr_lead_n", "pcr_all_k", "pcr_all_n", "pcr_manager_k", "pcr_manager_n", "pcr_lead_k",
    "pcr_lead_n", "any_false_done", "any_premature", "any_failure", "lead_done", "task_met", "task_total",
    "task_complete", "fid_claimed", "fid_claimed_true", "fid_met", "fid_met_claimed", "steps", "ticks", "effects",
    "suppressed", "blocked", "refused_reports", "llm_calls", "input_tokens", "output_tokens", "tokens",
    "estimated_token_calls", "parse_failures", "client_errors"]


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return f"{v:.6g}"
    return str(v)


def write_csv(path: Path, rows: list, columns: list | None = None) -> None:
    if columns is None:
        columns = []
        for r in rows:
            for k in r:
                if k not in columns:
                    columns.append(k)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([fmt(r.get(c)) for c in columns])
    path.write_text(buf.getvalue(), encoding="utf-8")


def _ci(v, lo, hi, pct=False, digits=1):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    s = 100.0 if pct else 1.0
    return f"{s * v:.{digits}f} [{s * lo:.{digits}f}, {s * hi:.{digits}f}]"


def _share(d, p):
    if not d[f"{p}_n"]:
        return "n/a"
    return f"{_ci(d[p], d[p + '_lo'], d[p + '_hi'], pct=True, digits=0)} ({d[p + '_k']}/{d[p + '_n']})"


def _pct(v):
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{100 * v:+.0f}%"


def table1_md(t1: list, rows: list, label: str = "") -> str:
    head = [f"# Table 1{(' - ' + label) if label else ''}", "",
            "Per model x fault profile x tree shape x condition. DAR: extra effective side effects per 100 required "
            "effects, pooled, with a seed-level bootstrap 95% interval. Shares are percentages with 95% Wilson "
            "intervals and the counts (k/n). FCR is over accepted done reports; PCR over done reports by nodes with "
            "children. Cost columns are means per episode; overhead is relative to B0 in the same model, faults and "
            "shape (tokens when the team used a model, else scheduler steps). `all` pools the shapes.", ""]
    cols = ["model", "faults", "shape", "cond", "n", "DAR/100 [95% CI]", "eps with dup", "worker FCR", "lead FCR",
            "PCR", "task success", "fidelity P", "fidelity R", "steps", "LLM calls", "tokens", "cost overhead"]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for d in t1:
        lines.append("| " + " | ".join([
            str(d["model"]), str(d["faults"]), str(d["shape"]), str(d["condition"]),
            f"{d['n_episodes']}" + (f" (+{d['n_excluded']} excl.)" if d["n_excluded"] else ""),
            _ci(d["dar_per100"], d["dar_per100_lo"], d["dar_per100_hi"]),
            _share(d, "dup_episodes"), _share(d, "fcr_worker"), _share(d, "fcr_lead"), _share(d, "pcr_all"),
            _share(d, "task_success"), _share(d, "fidelity_precision"), _share(d, "fidelity_recall"),
            f"{d['mean_steps']:.1f}", f"{d['mean_llm_calls']:.1f}", f"{d['mean_tokens']:.0f}",
            ("baseline" if d["condition"] == tables.BASELINE else f"{_pct(d['cost_overhead_vs_b0'])} {d['cost_measure']}"),
        ]) + " |")
    excl = [r for r in rows if not r["included"]]
    tail = ["", f"Episodes found: {len(rows)}; included: {len(rows) - len(excl)}; excluded: {len(excl)}."]
    if excl:
        tail += ["", "| excluded episode | reason |", "|---|---|"]
        tail += [f"| {r['run_dir']} | {r['excluded_reason']} |" for r in excl]
    maxt = sum(1 for r in rows if r["included"] and r.get("hit_max_ticks"))
    capt = sum(1 for r in rows if r["included"] and r.get("hit_call_cap"))
    if maxt or capt:
        tail += ["", f"Included episodes that ended at max_ticks: {maxt}; at the LLM call cap: {capt} "
                     "(kept and counted as outcomes)."]
    return "\n".join(head + lines + tail) + "\n"


def analyze(run_dirs: list, out: Path, boot_seed: int = 0, perm_seed: int = 0, n_boot: int = N_BOOT,
            n_perm: int = N_PERM, exclude_client_errors: bool = False, label: str = "", base: Path | None = None) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows, metrics, ledgers = load_rows(run_dirs, base=base, exclude_client_errors=exclude_client_errors)
    if not rows:
        raise SystemExit("no episode directories found under " + ", ".join(map(str, run_dirs)))
    t1 = tables.table1(rows, boot_seed=boot_seed, n_boot=n_boot)
    ts = tables.tests(rows, perm_seed=perm_seed, n_perm=n_perm)
    val = tables.validation(rows, metrics, ledgers, boot_seed=boot_seed, n_boot=n_boot)
    write_csv(out / "episodes.csv", rows, EPISODE_BASE + EPISODE_METRICS)
    write_csv(out / "table1.csv", t1)
    (out / "table1.md").write_text(table1_md(t1, rows, label), encoding="utf-8")
    write_csv(out / "tests.csv", ts)
    write_csv(out / "excluded.csv", [r for r in rows if not r["included"]],
              ["run_dir", "episode_id", "model", "condition", "shape", "seed", "excluded_reason"])
    if val:
        write_csv(out / "validation.csv", val)
    summary = {
        "label": label, "inputs": [str(p) for p in run_dirs], "episodes_found": len(rows),
        "episodes_included": sum(r["included"] for r in rows),
        "episodes_excluded": sum(1 - r["included"] for r in rows),
        "excluded_by_reason": _count(r["excluded_reason"] for r in rows if not r["included"]),
        "hit_max_ticks_included": sum(r.get("hit_max_ticks", 0) for r in rows if r["included"]),
        "hit_call_cap_included": sum(r.get("hit_call_cap", 0) for r in rows if r["included"]),
        "models": sorted({r["model"] for r in rows if r["included"]}),
        "fault_profiles": sorted({r["faults"] for r in rows if r["included"]}),
        "episode_git_commits": sorted({r["git_commit"] for r in rows if r["git_commit"]}),
        "bootstrap": {"seed": boot_seed, "n_boot": n_boot, "unit": "seed (cluster)"},
        "permutation": {"seed": perm_seed, "n_perm": n_perm},
        "exclude_client_errors": exclude_client_errors,
        "files": sorted(["episodes.csv", "table1.csv", "table1.md", "tests.csv", "excluded.csv"]
                        + (["validation.csv"] if val else [])),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"rows": rows, "table1": t1, "tests": ts, "validation": val, "summary": summary}


def _count(it):
    d = {}
    for x in it:
        d[x.split(":")[0]] = d.get(x.split(":")[0], 0) + 1
    return dict(sorted(d.items()))
