"""Command line entry point.

    python -m dst.run run --shape F --condition B0 --agents scripted-naive --seed 1 --out runs/demo
    python -m dst.run run --shape D --condition E3 --agents llm --provider P --model M --seed 1 --out runs/x
    python -m dst.run rerun --replay runs/demo [--out runs/demo-replay]
    python -m dst.run metrics runs/demo [runs/other ...]

``rerun --replay`` rebuilds the episode from its manifest: scripted agents from the
seed, LLM agents from the responses recorded in the episode log (no API calls), and
checks that episode.jsonl, scheduler.jsonl and ledger.json are byte-identical.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .agents.base import ABORT_REASONS, CallBudget
from .agents.llm_agent import LLMAgent
from .agents.scripted import make_scripted_agents
from .env import CONDITIONS, EnvConfig, FaultConfig, Office, build_tree
from .logging import LOG_FILES, MemoryLogger, base_manifest, load_episode, sha256_file
from .metrics import aggregate, episode_metrics
from .tasks import generate_batch

AGENT_KINDS = ("scripted-naive", "scripted-careful", "llm")
# LLM team defaults (see docs/PILOT.md): prompt budget per call (estimated input tokens), output
# limit, LLM calls per episode for the whole team, failed calls in a row before the episode stops
# (1: a call that still fails after the client's own retries and backoff stops the episode, so no
# valid episode contains a no-op caused by the provider; the rerun replays the cached calls).
LLM_DEFAULTS = {"max_prompt_tokens": 1200, "max_tokens": 256, "max_llm_calls": 200, "max_consecutive_errors": 1}


def get_llm_client(provider: str):
    """Client factory for live LLM runs. Imported lazily so nothing else needs dst.llm."""
    from dst.llm import get_client  # provided by the LLM layer
    return get_client(provider)


def episode_id_for(shape, condition, agents, seed, model=None) -> str:
    tag = agents if agents != "llm" else f"llm-{(model or 'model').replace('/', '_')}"
    return f"{shape}-{condition}-{tag}-s{seed}"


def run_episode(*, shape="F", condition="B0", agents="scripted-naive", seed=1, faults: FaultConfig | None = None,
                n_messages=12, n_repeat=3, turn_cap=40, max_ticks=300, lease_ticks=6, client=None,
                clients: dict | None = None, provider=None, model=None, temperature=0.7, history_turns=3,
                max_retries=3, out=None, episode_id=None, extra_manifest=None, max_prompt_tokens=LLM_DEFAULTS["max_prompt_tokens"],
                max_tokens=LLM_DEFAULTS["max_tokens"], max_llm_calls=LLM_DEFAULTS["max_llm_calls"],
                max_consecutive_errors=LLM_DEFAULTS["max_consecutive_errors"]):
    """Run one episode. Returns (logger, ledger, metrics). ``client`` is shared by all LLM
    agents; ``clients`` maps agent id -> client (used by replay).

    LLM teams: ``max_llm_calls`` caps the calls of the whole team (end reason ``call_cap``);
    a free-quota refusal or ``max_consecutive_errors`` failed calls of one agent stop the
    episode with end reason ``aborted_quota`` / ``aborted_client_error`` (an invalid sample:
    the manifest says ``aborted: true`` and a grid runner reruns it). ``max_prompt_tokens``
    bounds the estimated prompt per call by trimming old history; ``max_tokens`` is the
    output limit sent to the provider."""
    if condition not in CONDITIONS:
        raise ValueError(f"condition must be one of {list(CONDITIONS)}")
    if agents not in AGENT_KINDS:
        raise ValueError(f"agents must be one of {AGENT_KINDS}")
    faults = faults or FaultConfig()
    batch = generate_batch(seed, n_messages=n_messages, n_repeat=n_repeat)
    cfg = EnvConfig(shape=shape, condition=condition, faults=faults, turn_cap=turn_cap, max_ticks=max_ticks,
                    lease_ticks=lease_ticks)
    specs = build_tree(shape)
    if agents.startswith("scripted-"):
        team = make_scripted_agents(agents.split("-", 1)[1], specs, max_retries=max_retries)
    else:
        if clients is None:
            if client is None:
                if not provider:
                    raise ValueError("--agents llm needs --provider (or a client object)")
                client = get_llm_client(provider)
            clients = {s.id: client for s in specs}
        if not model:
            raise ValueError("--agents llm needs --model")
        budget = CallBudget(max_llm_calls)
        team = {s.id: LLMAgent(s.id, s.role, condition, clients[s.id], model, temperature=temperature, seed=seed,
                               history_turns=history_turns, max_prompt_tokens=max_prompt_tokens,
                               max_tokens=max_tokens, budget=budget, max_consecutive_errors=max_consecutive_errors)
                for s in specs}
    usage0 = _client_usage(client)
    eid = episode_id or episode_id_for(shape, condition, agents, seed, model)
    logger = MemoryLogger(eid, seed)
    office = Office(batch, cfg, seed, logger, eid)
    t0 = time.monotonic()
    ledger = office.run(team)
    wall_s = round(time.monotonic() - t0, 1)
    # the event log is only needed for LLM cost counters
    metrics = episode_metrics(ledger, logger.schedule_records(), logger.events() if agents == "llm" else None)
    if out is not None:
        manifest = base_manifest()
        manifest.update({
            "episode_id": eid, "seed": seed, "shape": shape, "condition": condition, "agents": agents,
            "provider": provider, "model": model, "temperature": temperature if agents == "llm" else None,
            "history_turns": history_turns if agents == "llm" else None, "max_retries": max_retries,
            "faults": faults.to_dict(), "turn_cap": turn_cap, "max_ticks": max_ticks, "lease_ticks": lease_ticks,
            "batch": {"n_messages": n_messages, "n_repeat": n_repeat, "task_id": batch.task_id},
            "agent_stats": {a: ag.stats() for a, ag in team.items() if ag.stats()},
            "end_reason": ledger["end"]["reason"], "aborted": ledger["end"]["reason"] in ABORT_REASONS,
            "wall_s": wall_s,
        })
        if agents == "llm":
            manifest["llm"] = {"max_prompt_tokens": max_prompt_tokens, "max_tokens": max_tokens,
                               "max_llm_calls": max_llm_calls, "max_consecutive_errors": max_consecutive_errors}
            usage1 = _client_usage(client)
            if usage0 is not None and usage1 is not None:
                # what the provider client did for this episode (network calls, cache hits, pacing wait)
                manifest["client_usage"] = {k: (round(usage1[k] - usage0.get(k, 0), 1) if isinstance(usage1[k], float)
                                                else usage1[k] - usage0.get(k, 0)) for k in usage1}
        if extra_manifest:
            manifest.update(extra_manifest)
        logger.write(out, manifest)
        Path(out, "metrics.json").write_text(json.dumps(metrics, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return logger, ledger, metrics


def _client_usage(client):
    u = getattr(client, "usage", None)
    if u is None or not hasattr(u, "as_dict"):
        return None
    return dict(u.as_dict())


def replay_episode(ep_dir, out=None) -> dict:
    """Rerun an episode from its directory and compare the logs byte for byte."""
    from .agents.replay import replay_clients
    ep_dir = Path(ep_dir)
    ep = load_episode(ep_dir)
    m = ep["manifest"]
    out = Path(out) if out else ep_dir.parent / (ep_dir.name + "-replay")
    clients = replay_clients(ep["events"]) if m["agents"] == "llm" else None
    if clients is not None:
        # every agent needs a client object even if it never called the model
        from .agents.replay import ReplayClient
        for s in build_tree(m["shape"]):
            clients.setdefault(s.id, ReplayClient([], s.id))
    run_episode(shape=m["shape"], condition=m["condition"], agents=m["agents"], seed=m["seed"],
                faults=FaultConfig(**m["faults"]), n_messages=m["batch"]["n_messages"],
                n_repeat=m["batch"]["n_repeat"], turn_cap=m["turn_cap"], max_ticks=m["max_ticks"],
                lease_ticks=m["lease_ticks"], clients=clients, provider=m.get("provider"), model=m.get("model"),
                temperature=m.get("temperature") if m.get("temperature") is not None else 0.7,
                history_turns=m["history_turns"] if m.get("history_turns") is not None else 8, max_retries=m.get("max_retries", 3),
                out=out, episode_id=m["episode_id"], extra_manifest={"replay_of": str(ep_dir)},
                **(m.get("llm") or {"max_prompt_tokens": None, "max_tokens": None, "max_llm_calls": None}))
    if clients is not None:
        left = {a: len(c.events) - c.i for a, c in clients.items() if c.i != len(c.events)}
        if left:
            raise RuntimeError(f"replay did not consume every recorded LLM call: {left}")
    report = {}
    for name in LOG_FILES:
        a, b = sha256_file(ep_dir / name), sha256_file(out / name)
        report[name] = {"original": a, "replay": b, "identical": a == b}
    return {"out": str(out), "files": report, "identical": all(v["identical"] for v in report.values())}


def _summary_line(metrics: dict) -> str:
    d, f, p, ts = metrics["dar"], metrics["fcr"]["all"], metrics["pcr"]["all"], metrics["task_success"]
    return (f"{metrics['episode_id']}: DAR {d['per100']:.1f}/100 ({d['extras']} extra of {d['required']} required) | "
            f"false dones {f[0]}/{f[1]} | premature {p[0]}/{p[1]} | task success {ts['met']}/{ts['total']} | "
            f"steps {metrics['cost']['steps']} | llm calls {metrics['cost']['llm_calls']} "
            f"(parse failures {metrics['cost']['parse_failures']}, tokens in {metrics['cost']['input_tokens']}) | "
            f"end {metrics['end_reason']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dst.run", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run one episode")
    r.add_argument("--shape", default="F", choices=["F", "D", "D+"])
    r.add_argument("--condition", default="B0", choices=list(CONDITIONS))
    r.add_argument("--agents", default="scripted-naive", choices=AGENT_KINDS)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--out", required=True)
    r.add_argument("--provider")
    r.add_argument("--model")
    r.add_argument("--temperature", type=float, default=0.7)
    r.add_argument("--history-turns", type=int, default=3)
    r.add_argument("--max-prompt-tokens", type=int, default=LLM_DEFAULTS["max_prompt_tokens"])
    r.add_argument("--max-tokens", type=int, default=LLM_DEFAULTS["max_tokens"], help="output tokens per call")
    r.add_argument("--max-llm-calls", type=int, default=LLM_DEFAULTS["max_llm_calls"], help="per episode, whole team")
    r.add_argument("--turn-cap", type=int, default=40)
    r.add_argument("--max-ticks", type=int, default=300)
    r.add_argument("--lease-ticks", type=int, default=6)
    r.add_argument("--max-retries", type=int, default=3, help="scripted naive retrier: retries after a timeout")
    r.add_argument("--messages", type=int, default=12)
    r.add_argument("--repeat", type=int, default=3, help="customers who write twice")
    r.add_argument("--p-timeout", type=float, default=FaultConfig.p_timeout)
    r.add_argument("--p-bounce", type=float, default=FaultConfig.p_bounce)
    r.add_argument("--p-stale", type=float, default=FaultConfig.p_stale_board)
    r.add_argument("--delivery-delay", type=int, default=FaultConfig.delivery_delay)
    rr = sub.add_parser("rerun", help="reproduce an episode from its directory")
    rr.add_argument("--replay", required=True, help="episode directory to replay")
    rr.add_argument("--out", help="where to write the replay (default <dir>-replay)")
    mt = sub.add_parser("metrics", help="print pooled metrics for episode directories")
    mt.add_argument("dirs", nargs="+")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        faults = FaultConfig(args.p_timeout, args.p_bounce, args.p_stale, args.delivery_delay)
        _, _, metrics = run_episode(
            shape=args.shape, condition=args.condition, agents=args.agents, seed=args.seed, faults=faults,
            n_messages=args.messages, n_repeat=args.repeat, turn_cap=args.turn_cap, max_ticks=args.max_ticks,
            lease_ticks=args.lease_ticks, provider=args.provider, model=args.model, temperature=args.temperature,
            history_turns=args.history_turns, max_retries=args.max_retries, out=args.out,
            max_prompt_tokens=args.max_prompt_tokens, max_tokens=args.max_tokens, max_llm_calls=args.max_llm_calls)
        print(_summary_line(metrics))
        if metrics["end_reason"] in ABORT_REASONS:
            print(f"episode ABORTED ({metrics['end_reason']}): not a valid sample; rerun it later")
            return 3
        print(f"wrote {args.out}/ (episode.jsonl scheduler.jsonl ledger.json manifest.json metrics.json)")
        return 0
    if args.cmd == "rerun":
        res = replay_episode(args.replay, args.out)
        for name, v in res["files"].items():
            print(f"{name}: {'IDENTICAL' if v['identical'] else 'DIFFERENT'} sha256 {v['replay'][:16]}")
        print(("replay identical: " if res["identical"] else "replay DIFFERS: ") + res["out"])
        return 0 if res["identical"] else 1
    if args.cmd == "metrics":
        ms = []
        for d in args.dirs:
            ep = load_episode(d)
            m = episode_metrics(ep["ledger"], ep["schedule"], ep["events"])
            print(_summary_line(m))
            ms.append(m)
        print(json.dumps(aggregate(ms), indent=1, default=str))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
