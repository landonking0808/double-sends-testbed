#!/usr/bin/env python3
"""Resumable grid runner for LLM teams, paced to the providers' free limits.

    python scripts/run_grid.py --model A --shapes F,D --conditions B0,P1,E1,E2,E3 --seeds 1-8 \\
        --out runs/grid/A [--parallel 3] [--deadline-min 9] [--config configs/frozen.json]

* One directory per episode: ``<out>/<shape>-<condition>-s<seed>/`` (the files of ``dst.run``).
* Resumable: an episode whose ``manifest.json`` exists and is not ``aborted`` is skipped. An
  aborted episode (free quota reached, provider kept failing, deadline) is rerun on the next
  invocation; every LLM response is cached (``runs/cache/``), so the rerun replays the calls it
  already made for free and continues live from there.
* Pacing: the provider client holds every per-minute limit at 80% and every daily quota at 90%
  (``dst/llm/limits.py``), shared across threads and processes through locked state files.
  ``--parallel`` episodes run at once (each sequential inside, so each stays reproducible);
  they only overlap waiting on the API.
* Stops cleanly: when one episode hits the daily free quota (``aborted_quota``), every running
  episode stops at its next call and no new one starts. ``--deadline-min`` stops the same way
  after that many minutes (end reason ``aborted_deadline``), so a long grid can be driven in
  chunks. Exit code 0 = grid complete, 3 = stopped with episodes left (rerun to resume).
* Writes ``<out>/grid_manifest.json``: models, settings, per-episode status and key metrics,
  totals, wall times (US Central).

Fault rates and LLM settings come from ``--config`` (default ``configs/frozen.json``); the
``--p-*`` flags override them (pilot only).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dst.agents.base import ABORT_REASONS  # noqa: E402
from dst.env import CONDITIONS, FaultConfig  # noqa: E402
from dst.run import LLM_DEFAULTS, run_episode  # noqa: E402

CT = ZoneInfo("America/Chicago")
MODELS = {
    "A": ("gemini", "gemma-4-26b-a4b-it"),
    "B": ("cloudflare", "@cf/ibm-granite/granite-4.0-h-micro"),
    "fake": ("fake", "fake-model"),    # offline tests only
}
DEFAULT_PARALLEL = {"A": 3, "B": 2, "fake": 2}


def now_ct() -> str:
    return datetime.now(CT).isoformat(timespec="seconds")


def parse_seeds(spec: str) -> list:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return sorted(set(out))


class StopFlag(Exception):
    """Raised by the gated client when the grid is stopping; the episode ends aborted."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.abort_reason = reason


class GatedClient:
    """Wraps one provider client for one episode: refuses new calls once the grid stops."""

    def __init__(self, inner, stop: threading.Event, why: dict):
        self._inner, self._stop, self._why = inner, stop, why

    def complete(self, messages, **kw):
        if self._stop.is_set():
            raise StopFlag(self._why.get("reason", "aborted_deadline"))
        return self._inner.complete(messages, **kw)

    @property
    def last_usage(self):
        return getattr(self._inner, "last_usage", None)

    @last_usage.setter
    def last_usage(self, v):
        self._inner.last_usage = v

    def __getattr__(self, name):
        return getattr(self._inner, name)


def make_client(provider: str, model_key: str):
    from dst.llm import get_client
    if provider == "fake":
        return get_client("fake")
    return get_client(provider)


def load_config(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def episode_dir(out: Path, shape: str, cond: str, seed: int) -> Path:
    return out / f"{shape}-{cond}-s{seed}"


def finished(d: Path) -> bool:
    m = d / "manifest.json"
    if not m.exists():
        return False
    try:
        man = json.loads(m.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return not man.get("aborted") and man.get("end_reason") not in ABORT_REASONS


def summarize(d: Path) -> dict:
    try:
        man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        met = json.loads((d / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing"}
    c = met["cost"]
    return {"status": "aborted" if man.get("aborted") else "done", "end_reason": met["end_reason"],
            "dar_extras": met["dar"]["extras"], "dar_required": met["dar"]["required"],
            "false_dones": met["fcr"]["all"], "premature": met["pcr"]["all"],
            "task_success": [met["task_success"]["met"], met["task_success"]["total"]],
            "llm_calls": c["llm_calls"], "input_tokens": c["input_tokens"], "output_tokens": c["output_tokens"],
            "parse_failures": c["parse_failures"], "client_errors": c["client_errors"],
            "wall_s": man.get("wall_s"), "created_at": man.get("created_at")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", required=True, choices=sorted(MODELS))
    ap.add_argument("--shapes", default="F,D")
    ap.add_argument("--conditions", default=",".join(CONDITIONS))
    ap.add_argument("--seeds", default="1-8")
    ap.add_argument("--out", required=True)
    ap.add_argument("--config", default=str(ROOT / "configs" / "frozen.json"))
    ap.add_argument("--parallel", type=int, default=None)
    ap.add_argument("--deadline-min", type=float, default=None, help="stop cleanly after this many minutes")
    ap.add_argument("--p-timeout", type=float)
    ap.add_argument("--p-bounce", type=float)
    ap.add_argument("--p-stale", type=float)
    ap.add_argument("--repeat", type=int, help="customers who write twice (overlap)")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, call nothing")
    args = ap.parse_args(argv)

    provider, model = MODELS[args.model]
    cfg = load_config(Path(args.config))
    f = dict(FaultConfig().to_dict(), **cfg.get("faults", {}))
    for k, flag in (("p_timeout", args.p_timeout), ("p_bounce", args.p_bounce), ("p_stale_board", args.p_stale)):
        if flag is not None:
            f[k] = flag
    faults = FaultConfig(**f)
    batch = dict({"n_messages": 12, "n_repeat": 3}, **cfg.get("batch", {}))
    if args.repeat is not None:
        batch["n_repeat"] = args.repeat
    llm = dict(LLM_DEFAULTS, **cfg.get("llm", {}))
    env = dict({"turn_cap": 40, "max_ticks": 300, "lease_ticks": 6}, **cfg.get("env", {}))
    temperature = cfg.get("temperature", 0.7)
    history_turns = cfg.get("history_turns", 3)

    shapes = [s.strip() for s in args.shapes.split(",") if s.strip()]
    conds = [c.strip() for c in args.conditions.split(",") if c.strip()]
    bad = [c for c in conds if c not in CONDITIONS]
    if bad:
        ap.error(f"unknown conditions {bad}")
    seeds = parse_seeds(args.seeds)
    out = Path(args.out)
    cells = [(sh, c, s) for s in seeds for sh in shapes for c in conds]   # seed-major: partial grids stay balanced
    todo = [x for x in cells if not finished(episode_dir(out, *x))]
    par = args.parallel or DEFAULT_PARALLEL[args.model]
    print(f"[{now_ct()}] grid {args.model} ({provider}:{model}): {len(cells)} episodes, {len(cells) - len(todo)} "
          f"finished, {len(todo)} to run, parallel {par}; faults {faults.to_dict()}", flush=True)
    if args.dry_run:
        for x in todo:
            print("  would run", episode_dir(out, *x).name)
        return 0

    out.mkdir(parents=True, exist_ok=True)
    stop, why = threading.Event(), {}
    t_start = time.monotonic()
    started_ct = now_ct()
    deadline = t_start + args.deadline_min * 60 if args.deadline_min else None
    lock = threading.Lock()

    def run_one(cell):
        shape, cond, seed = cell
        if stop.is_set():
            return cell, None
        d = episode_dir(out, *cell)
        client = GatedClient(make_client(provider, args.model), stop, why)
        _, _, met = run_episode(
            shape=shape, condition=cond, agents="llm", seed=seed, faults=faults,
            n_messages=batch["n_messages"], n_repeat=batch["n_repeat"], turn_cap=env["turn_cap"],
            max_ticks=env["max_ticks"], lease_ticks=env["lease_ticks"], client=client, provider=provider,
            model=model, temperature=temperature, history_turns=history_turns, out=d,
            episode_id=f"{shape}-{cond}-{args.model}-s{seed}",
            max_prompt_tokens=llm["max_prompt_tokens"], max_tokens=llm["max_tokens"],
            max_llm_calls=llm["max_llm_calls"], max_consecutive_errors=llm["max_consecutive_errors"],
            extra_manifest={"grid": {"model_key": args.model, "config": args.config if cfg else None}})
        if met["end_reason"] == "aborted_quota":
            with lock:
                why["reason"] = "aborted_quota"
                stop.set()
        return cell, met

    def watchdog():
        while not stop.is_set():
            if deadline and time.monotonic() > deadline:
                with lock:
                    why.setdefault("reason", "aborted_deadline")
                    stop.set()
                return
            time.sleep(1.0)

    threading.Thread(target=watchdog, daemon=True).start()
    with ThreadPoolExecutor(max_workers=par) as ex:
        futs = [ex.submit(run_one, x) for x in todo]
        for fu in as_completed(futs):
            try:
                cell, met = fu.result()
            except Exception as e:  # noqa: BLE001 - one broken episode must not lose the others
                print(f"[{now_ct()}] episode failed: {type(e).__name__}: {e}", flush=True)
                continue
            if met is None:
                continue
            c = met["cost"]
            print(f"[{now_ct()}] {episode_dir(out, *cell).name}: end {met['end_reason']} | dup {met['dar']['extras']} | "
                  f"false done {met['fcr']['all'][0]}/{met['fcr']['all'][1]} | success "
                  f"{met['task_success']['met']}/{met['task_success']['total']} | calls {c['llm_calls']} | "
                  f"tokens in {c['input_tokens']}", flush=True)
    stop.set()

    eps = {episode_dir(out, *x).name: summarize(episode_dir(out, *x)) for x in cells}
    done = [v for v in eps.values() if v.get("status") == "done"]
    left = len(cells) - len(done)
    prev = {}
    mpath = out / "grid_manifest.json"
    if mpath.exists():
        try:
            prev = json.loads(mpath.read_text(encoding="utf-8"))
        except ValueError:
            prev = {}
    invocations = prev.get("invocations", []) + [{
        "started_ct": started_ct, "ended_ct": now_ct(), "wall_s": round(time.monotonic() - t_start, 1),
        "parallel": par, "ran": len(todo), "stop_reason": why.get("reason")}]
    manifest = {
        "model_key": args.model, "provider": provider, "model": model, "shapes": shapes, "conditions": conds,
        "seeds": seeds, "faults": faults.to_dict(), "batch": batch, "llm": llm, "env": env,
        "temperature": temperature, "history_turns": history_turns, "config": args.config if cfg else None,
        "episodes_total": len(cells), "episodes_done": len(done), "episodes_left": left,
        "totals_done": {k: sum(v[k] for v in done) for k in ("llm_calls", "input_tokens", "output_tokens",
                                                             "parse_failures", "client_errors")},
        "invocations": invocations, "episodes": eps,
    }
    mpath.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[{now_ct()}] {len(done)}/{len(cells)} episodes done; {left} left"
          + (f"; stopped: {why['reason']}" if why.get("reason") and left else "") + f"; manifest {mpath}", flush=True)
    return 0 if left == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
