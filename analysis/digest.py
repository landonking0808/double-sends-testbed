"""Human-readable digest of one episode for a manual audit of the computed labels.

The digest lists the settings, the tree, every turn (tool call, result, the ledger record
it touched), the ledger's effects with their delivery history, every report, and then the
labels ``dst.metrics`` computed: each duplicate and each false or premature done with its
agent and step. An auditor checks each label against the timeline and marks it in AUDIT.md.
"""
from __future__ import annotations

import json

from dst.logging import load_episode
from dst.metrics import episode_metrics


def _short(obj, n=110) -> str:
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False) if not isinstance(obj, str) else obj
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


def _call(call) -> str:
    """``tool k=v ...`` with long values shortened; a malformed call is shown as given."""
    if not isinstance(call, dict) or "tool" not in call:
        return "malformed: " + _short(call, 80)
    args = call.get("args") if isinstance(call.get("args"), dict) else {"args": call.get("args")}
    parts = [f"{k}={_short(v, 28)}" for k, v in sorted(args.items())]
    return _short(f"{call['tool']} " + " ".join(parts), 120)


def labels(m: dict) -> list:
    """Flat list of labels: (label_id, kind, agent, step, tick, detail)."""
    out = []
    for i, d in enumerate(m["attribution"]["duplicates"], 1):
        out.append((f"DUP{i}", "duplicate", d["agent"], d["step"], d["t"],
                    f"{d['intent']} to {d['target']}: effect {d['effect']} repeats {d['first_effect']} "
                    f"(first by {d['first_agent']}; {d['cause']})"))
    for i, d in enumerate(m["attribution"]["false_dones"], 1):
        out.append((f"FD{i}", "false_done", d["agent"], d["step"], d["t"],
                    f"report {d['report']} ({d['role']}) said done; unmet at that time: {', '.join(d['unmet'])}"))
    for i, d in enumerate(m["attribution"]["premature"], 1):
        out.append((f"PD{i}", "premature_done", d["agent"], d["step"], d["t"],
                    f"report {d['report']} ({d['role']}) said done while running: {', '.join(d['running'])}"))
    return out


def digest(ep_dir) -> tuple:
    """Returns (markdown, episode_id, labels)."""
    ep = load_episode(ep_dir)
    led, man = ep["ledger"], ep["manifest"]
    m = episode_metrics(led, ep["schedule"], ep["events"])
    eid = led["episode_id"]
    effects = {e["id"]: e for e in led["effects"]}
    L = [f"# Episode {eid}", "",
         f"- directory: `{ep_dir}`",
         f"- shape {led['shape']}, condition {led['condition']}, seed {led['seed']}, agents "
         f"{man.get('agents', '?')}" + (f", model {man.get('model')}" if man.get("model") else ""),
         f"- faults: {_short(led['config'].get('faults', {}))}",
         f"- end: {led['end']['reason']} at tick {led['end']['t']}; unfinished: {led['end'].get('unfinished') or 'none'}",
         f"- summary: DAR {m['dar']['per100']:.1f}/100 ({m['dar']['extras']} extra of {m['dar']['required']} required); "
         f"false dones {m['fcr']['all'][0]}/{m['fcr']['all'][1]}; premature {m['pcr']['all'][0]}/{m['pcr']['all'][1]}; "
         f"task success {m['task_success']['met']}/{m['task_success']['total']}",
         "", "## Tree and assignments", "", "| agent | role | parent | items |", "|---|---|---|---|"]
    for a in led["agents"]:
        L.append(f"| {a['id']} | {a['role']} | {a['parent'] or '-'} | {', '.join(led['items'].get(a['id'], [])) or '-'} |")
    L += ["", "## Messages (customer -> message ids)", ""]
    by_c = {}
    for msg in led["batch"]["messages"]:
        by_c.setdefault(msg["customer"], []).append(msg["id"])
    L.append("; ".join(f"{c}: {', '.join(v)}" for c, v in sorted(by_c.items())))
    L += ["", "## Requirements", "", "| id | kind | amount | met at end |", "|---|---|---|---|"]
    from dst.tasks import requirement_met
    for r in led["requirements"]:
        L.append(f"| {r['id']} | {r['kind']} | {r.get('amount') if r.get('amount') is not None else '-'} | "
                 f"{'yes' if requirement_met(r, led['effects']) else 'NO'} |")

    L += ["", "## Timeline (one row per turn)", "",
          "| t | agent | step | call | result | ledger |", "|---|---|---|---|---|---|"]
    pending = {}
    for ev in ep["events"]:
        p = ev["payload"]
        if ev["event"] == "tool_call":
            pending[ev["agent"]] = (ev["t"], p.get("step"), p.get("call"))
        elif ev["event"] == "tool_result":
            t, step, call = pending.pop(ev["agent"], (ev["t"], p.get("step"), None))
            ref = ev.get("ledger_ref") or ""
            if ref in effects:
                e = effects[ref]
                ref = f"{ref} {e['intent']}->{e['target']} final {e['final']}"
            L.append(f"| {t} | {ev['agent']} | {step} | {_call(call)} | {_short(p.get('result'), 70)} | {ref} |")
        elif ev["event"] == "message" and p.get("kind") == "child_stopped":
            L.append(f"| {ev['t']} | env | - | child_stopped {p.get('child')} ({p.get('reason')}) | | |")

    L += ["", "## Ledger effects (ground truth)", "",
          "| id | seq | t | agent | step | tool | target | intent | returned | history | final | key |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in sorted(led["effects"], key=lambda e: e["seq"]):
        hist = " ".join(f"{t}:{s}" for t, s in e.get("history", []))
        L.append(f"| {e['id']} | {e['seq']} | {e['t']} | {e['agent']} | {e['step']} | {e['tool']} | {e['target']} | "
                 f"{e['intent']} | {e['returned']} | {hist} | {e['final']} | {e.get('key') or '-'} |")
    if led.get("suppressed") or led.get("blocked"):
        L += ["", f"E1: {len(led.get('suppressed', []))} suppressed and {len(led.get('blocked', []))} blocked calls "
                  "(not applied)."]

    L += ["", "## Reports", "", "| id | seq | t | agent | step | status | accepted | reason | claims | text |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(led["reports"], key=lambda r: r["seq"]):
        claims = "-" if r.get("claims") is None else ", ".join(f"{c.get('intent')}:{c.get('target')}" for c in r["claims"])
        L.append(f"| {r['id']} | {r['seq']} | {r['t']} | {r['agent']} | {r['step']} | {r['status']} | "
                 f"{'yes' if r['accepted'] else 'no'} | {r.get('reason') or '-'} | {_short(claims, 80)} | "
                 f"{_short(r.get('text') or '', 60)} |")

    labs = labels(m)
    L += ["", "## Computed labels (check each against the timeline)", ""]
    if labs:
        L += ["| label | kind | agent | step | t | detail |", "|---|---|---|---|---|---|"]
        L += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in labs]
    else:
        L.append("No duplicates, false dones or premature dones were labeled.")
    L += ["", "Also check for misses: a repeated effective effect, or a done report with unmet requirements or "
              "running descendants, that has no label above."]
    return "\n".join(L) + "\n", eid, labs
