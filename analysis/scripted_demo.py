"""Generate scripted-agent episodes (no LLM, no network) for the demo results and tests.

    python -m analysis.scripted_demo --out runs/scripted-demo --seeds 1-10

Writes one episode directory per (agents, shape, condition, seed), exactly as
``python -m dst.run run --agents scripted-naive|scripted-careful ...`` would (same
``dst.run.run_episode``, default fault rates), named ``<agents>/<episode_id>``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dst.run import episode_id_for, run_episode

AGENTS = ("scripted-naive", "scripted-careful")
SHAPES = ("F", "D", "D+")
CONDITIONS = ("B0", "P1", "E1", "E2", "E3")


def parse_seeds(s: str) -> list:
    out = []
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def generate(out, seeds, agents=AGENTS, shapes=SHAPES, conditions=CONDITIONS, **kw) -> list:
    out = Path(out)
    dirs = []
    for ag in agents:
        for sh in shapes:
            for cond in conditions:
                for s in seeds:
                    d = out / ag / episode_id_for(sh, cond, ag, s)
                    run_episode(shape=sh, condition=cond, agents=ag, seed=s, out=d, **kw)
                    dirs.append(d)
    return dirs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", default="1-10", help="e.g. 1-10 or 1,2,5")
    ap.add_argument("--agents", default=",".join(AGENTS))
    ap.add_argument("--shapes", default=",".join(SHAPES))
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    a = ap.parse_args(argv)
    dirs = generate(a.out, parse_seeds(a.seeds), a.agents.split(","), a.shapes.split(","), a.conditions.split(","))
    print(f"wrote {len(dirs)} scripted episodes under {a.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
