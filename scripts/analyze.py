#!/usr/bin/env python3
"""Analyze episode logs: per-episode table, Table 1 with intervals, B0-vs-control tests.

    python scripts/analyze.py runs/pilot [more run dirs ...] --out results/

Writes to --out: episodes.csv (one row per episode directory found, excluded ones included
with their reason), table1.csv + table1.md, tests.csv, excluded.csv, validation.csv (only
when scripted teams are present) and summary.json. Every number comes from the episode
logs via dst.metrics; bootstrap and permutation draws use fixed seeds, so the output is
byte-identical for the same inputs.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.report import analyze  # noqa: E402
from analysis.stats import N_BOOT, N_PERM  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="directories holding episode directories (searched recursively)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default="", help="title tag for table1.md, e.g. 'scripted demo'")
    ap.add_argument("--boot-seed", type=int, default=0)
    ap.add_argument("--perm-seed", type=int, default=0)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--n-perm", type=int, default=N_PERM)
    ap.add_argument("--exclude-client-errors", action="store_true",
                    help="exclude episodes in which an LLM call raised (default: keep and count them)")
    a = ap.parse_args(argv)
    res = analyze([Path(p) for p in a.runs], Path(a.out), boot_seed=a.boot_seed, perm_seed=a.perm_seed,
                  n_boot=a.n_boot, n_perm=a.n_perm, exclude_client_errors=a.exclude_client_errors, label=a.label)
    s = res["summary"]
    print(f"episodes found {s['episodes_found']}, included {s['episodes_included']}, excluded {s['episodes_excluded']}"
          + (f" {s['excluded_by_reason']}" if s["episodes_excluded"] else ""))
    print(f"cells {len(res['table1'])}, tests {len(res['tests'])}, validation rows {len(res['validation'])}")
    print(f"wrote {a.out}/ ({', '.join(s['files'])}, summary.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
