#!/usr/bin/env python3
"""Write audit digests for a random sample of episodes, for a manual check of the labels.

    python scripts/audit_sample.py runs/pilot --n 10 --seed 1 [--out audit/]

For each sampled episode (seeded; replay copies skipped) writes ``<out>/<episode>.md``: the
settings, tree, a turn-by-turn timeline (tool calls, results, ledger records), the ledger
effects, the reports, and every computed label (each duplicate, false done and premature
done, with agent and step). Also writes ``<out>/AUDIT.md``, a blank table with one row per
label (agree / disagree / note) plus one "missed labels" row per episode.
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.digest import digest  # noqa: E402
from analysis.load import original_episode_dirs  # noqa: E402


def safe_name(eid: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.+" else "_" for ch in eid)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", default="audit")
    a = ap.parse_args(argv)
    eps = original_episode_dirs(a.runs)
    if not eps:
        print("no episodes found")
        return 2
    pick = sorted(random.Random(a.seed).sample(eps, min(a.n, len(eps))), key=str)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for d in pick:
        md, eid, labs = digest(d)
        name = safe_name(eid)
        if (out / f"{name}.md").exists() and any(r[0] == name for r in rows):
            name += "-" + safe_name(d.parent.name)
        (out / f"{name}.md").write_text(md, encoding="utf-8")
        rows.append((name, labs))
        print(f"wrote {out / (name + '.md')} ({len(labs)} labels)")
    A = ["# Manual audit of computed labels", "",
         f"Sample: {len(pick)} of {len(eps)} episodes from {', '.join(a.runs)} (sample seed {a.seed}).",
         "For each label, read the episode digest and mark `agree` or `disagree` (with a note). In each episode's "
         "`missed` row, list any duplicate, false done or premature done the metrics did not label (or `none`).", "",
         "| episode | label | kind | agent | step | agree/disagree | note |", "|---|---|---|---|---|---|---|"]
    for name, labs in rows:
        for lab, kind, agent, step, _t, _detail in labs:
            A.append(f"| [{name}]({name}.md) | {lab} | {kind} | {agent} | {step} |  |  |")
        A.append(f"| [{name}]({name}.md) | missed | - | - | - |  |  |")
    n_labels = sum(len(l) for _, l in rows)
    A += ["", f"Labels to check: {n_labels}. Agreement = agree / (agree + disagree); report it with the missed count."]
    (out / "AUDIT.md").write_text("\n".join(A) + "\n", encoding="utf-8")
    print(f"wrote {out / 'AUDIT.md'} ({n_labels} labels, {len(rows)} episodes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
