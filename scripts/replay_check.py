#!/usr/bin/env python3
"""Replay a random sample of episodes and check the logs are byte-identical.

    python scripts/replay_check.py runs/pilot --n 3 --seed 0

Picks --n episodes at random (seeded; replay copies are skipped), reruns each with
``dst.run.replay_episode`` into a temporary directory (scripted teams from the seed, LLM
teams from the responses recorded in their log, no API calls) and compares
episode.jsonl, scheduler.jsonl and ledger.json by SHA-256. Exit 0 only if every replay is
identical.
"""
import argparse
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.load import original_episode_dirs  # noqa: E402
from dst.run import replay_episode  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    eps = original_episode_dirs(a.runs)
    if not eps:
        print("no episodes found")
        return 2
    pick = sorted(random.Random(a.seed).sample(eps, min(a.n, len(eps))), key=str)
    print(f"replay check: {len(pick)} of {len(eps)} episodes (sample seed {a.seed})")
    ok = True
    with tempfile.TemporaryDirectory(prefix="dst-replay-") as tmp:
        for i, d in enumerate(pick):
            try:
                res = replay_episode(d, Path(tmp) / f"r{i}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"  ERROR     {d}: {type(e).__name__}: {e}")
                continue
            ok &= res["identical"]
            files = ", ".join(f"{n} {'=' if v['identical'] else 'DIFF'} {v['original'][:12]}"
                              for n, v in res["files"].items())
            print(f"  {'IDENTICAL' if res['identical'] else 'DIFFERENT'} {d}: {files}")
    print("result: " + ("all replays byte-identical" if ok else "REPLAY MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
