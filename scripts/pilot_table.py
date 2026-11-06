#!/usr/bin/env python3
"""Per-condition pilot table from grid episode directories (finished episodes only).

    python scripts/pilot_table.py runs/pilot/r1/A [runs/pilot/r1/B ...] [--md]

Columns: n episodes, episodes with a duplicate, DAR per 100 required effects (pooled), false
done reports / accepted done reports (FCR), premature lead/manager dones (PCR), task success
(requirements met / total), mean LLM calls, input and output tokens per episode, parse failures
per call, mean wall time per episode. Everything is computed by dst.metrics from the logs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dst.logging import load_episode  # noqa: E402
from dst.metrics import episode_metrics  # noqa: E402

ORDER = ["B0", "P1", "E1", "E2", "E3"]


def rows(grid_dir: Path) -> list:
    out = []
    for d in sorted(p for p in grid_dir.iterdir() if (p / "manifest.json").exists()):
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        if man.get("aborted"):
            continue
        ep = load_episode(d)
        m = episode_metrics(ep["ledger"], ep["schedule"], ep["events"])
        m["wall_s"] = man.get("wall_s")
        out.append(m)
    return out


def table(ms: list) -> list:
    res = []
    for c in ORDER + sorted({m["condition"] for m in ms} - set(ORDER)):
        cell = [m for m in ms if m["condition"] == c]
        if not cell:
            continue
        n = len(cell)
        calls = sum(m["cost"]["llm_calls"] for m in cell)
        res.append({
            "condition": c, "n": n,
            "eps_dup": sum(m["dar"]["any_duplicate"] for m in cell),
            "dar": 100.0 * sum(m["dar"]["extras"] for m in cell) / max(1, sum(m["dar"]["required"] for m in cell)),
            "fcr": (sum(m["fcr"]["all"][0] for m in cell), sum(m["fcr"]["all"][1] for m in cell)),
            "pcr": (sum(m["pcr"]["all"][0] for m in cell), sum(m["pcr"]["all"][1] for m in cell)),
            "success": (sum(m["task_success"]["met"] for m in cell), sum(m["task_success"]["total"] for m in cell)),
            "calls": calls / n,
            "in_tok": sum(m["cost"]["input_tokens"] for m in cell) / n,
            "out_tok": sum(m["cost"]["output_tokens"] for m in cell) / n,
            "parse_fail": (sum(m["cost"]["parse_failures"] for m in cell), calls),
            "wall_min": sum((m["wall_s"] or 0) for m in cell) / n / 60,
            "ends": sorted({m["end_reason"] for m in cell}),
        })
    return res


def fmt(t: list, md: bool) -> str:
    head = ["cond", "n", "eps w/ dup", "DAR/100", "FCR", "PCR", "success", "calls/ep", "in tok/ep", "out tok/ep",
            "parse fail", "wall min/ep*", "end"]
    lines = []
    for r in t:
        lines.append([r["condition"], str(r["n"]), f"{r['eps_dup']}/{r['n']}", f"{r['dar']:.1f}",
                      f"{r['fcr'][0]}/{r['fcr'][1]}", f"{r['pcr'][0]}/{r['pcr'][1]}",
                      f"{r['success'][0]}/{r['success'][1]} ({100 * r['success'][0] / max(1, r['success'][1]):.0f}%)",
                      f"{r['calls']:.0f}", f"{r['in_tok']:.0f}", f"{r['out_tok']:.0f}",
                      f"{r['parse_fail'][0]}/{r['parse_fail'][1]}", f"{r['wall_min']:.1f}", ",".join(r["ends"])])
    if md:
        return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
                         + ["| " + " | ".join(x) + " |" for x in lines])
    w = [max(len(h), *(len(x[i]) for x in lines)) for i, h in enumerate(head)] if lines else [len(h) for h in head]
    return "\n".join(["  ".join(h.ljust(w[i]) for i, h in enumerate(head))]
                     + ["  ".join(c.ljust(w[i]) for i, c in enumerate(x)) for x in lines])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    md = "--md" in argv
    for a in [a for a in argv if a != "--md"]:
        ms = rows(Path(a))
        print(f"\n{a}: {len(ms)} finished episodes")
        print(fmt(table(ms), md))
    print("\n* wall time of the last invocation that ran the episode (replayed cached calls are fast).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
