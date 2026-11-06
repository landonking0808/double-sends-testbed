#!/usr/bin/env python3
"""Make the paper figures from an analysis directory (output of scripts/analyze.py).

    python scripts/make_figures.py results/ --out figures/

Fig 1 failures (DAR, worker and lead FCR) by condition per model; Fig 2 cost overhead vs
failure reduction; Fig 3 failures by tree shape; plus the scripted-agent validation
figure (measured vs closed-form) when results/validation.csv exists. PDF + PNG.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.figures import make_all  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results", help="directory with table1.csv (and validation.csv)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", default=None, help="tag shown in figure titles (default: summary.json label)")
    a = ap.parse_args(argv)
    paths = make_all(Path(a.results), Path(a.out), a.label)
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
