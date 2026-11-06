"""Build what an overseer sees of an episode, without the ledger.

Two views (see docs/MONITOR.md for the exact in/out list):

* ``report`` - agent communications only: the task header (task id, message id -> customer:
  the input the owner gave, taken from ``ledger.json["batch"]`` and nothing else), every
  delegation, every delivered report (status, text, the effects the agent claimed), every
  board post (claim / note, as posted), and turn-cap notices delivered to a parent.
* ``full``   - the report view plus every tool call each agent made and every tool result
  exactly as that agent saw it (status ``ok``/``queued``/``timeout``/``refused``/..., verify
  read-backs included, since agents saw them).

Never in either view: ``ledger.json`` (beyond the task input above), ``scheduler.jsonl``, ``metrics.json``, the manifest
(condition label, fault rates), ``ledger_ref`` ids, ``llm_call`` events (raw responses,
prompts, token counts), board visibility ticks (the stale-board fault), refused reports
in the report view (they were never delivered), and delivery outcomes (bounces) except
where an agent's own ``verify`` result showed them in the full view.

Rendering is a deterministic list of short text lines; ``render(view, budget_tokens)``
trims deterministically (see ``trim``) so the prompt fits the token budget.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

VIEWS = ("report", "full")
SIDE_EFFECT_TOOLS = ("send_message", "issue_refund", "update_record")
COORD_TOOLS = ("claim", "post_board", "delegate", "report")
READ_TOOLS = ("read_inbox", "read_board", "lookup", "verify", "noop")

# Line priority for trimming: lower = dropped first. Communications and side-effect
# calls/results are never dropped (only shortened).
PRI_READ_RESULT = 0      # read_inbox / read_board / lookup results, noop
PRI_READ_CALL = 1        # read_inbox / read_board / lookup / noop calls
PRI_VERIFY = 2           # verify calls and results (agents' read-back)
PRI_KEEP = 9


def est_tokens(text: str) -> int:
    """Same conservative estimate the client uses before a call (about 3.5 chars a token)."""
    return max(1, math.ceil(len(text) / 3.5))


def _j(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _short(s, n: int) -> str:
    s = "" if s is None else str(s)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


def load_events(ep_dir: Path) -> list:
    with open(Path(ep_dir) / "episode.jsonl", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def task_input(ep_dir: Path) -> dict:
    """The task the owner gave the team: task id and message id -> customer, nothing else.

    It is read from ``ledger.json["batch"]`` because the lead's first observation (which
    holds it) is not logged as text. Only the task input is taken: no message texts, no
    order data, no policy, no requirements, no effects or reports."""
    with open(Path(ep_dir) / "ledger.json", encoding="utf-8") as f:
        batch = json.load(f)["batch"]
    return {"task_id": batch.get("task_id"),
            "messages": [{"id": m["id"], "customer": m["customer"]} for m in batch.get("messages", [])]}


def _task_header(task) -> list:
    if not task:
        return []
    pairs = " ".join(f"{m['id']}={m['customer']}" for m in task["messages"])
    n_c = len({m["customer"] for m in task["messages"]})
    return [f"TASK {task.get('task_id') or ''}: {len(task['messages'])} customer messages from {n_c} customers "
            f"(id=customer): {pairs}"]


def _fmt_result(tool: str, res: dict) -> str:
    """Compact rendering of a tool result as the agent saw it."""
    if not isinstance(res, dict):
        return _short(res, 120)
    st = res.get("status")
    if tool == "read_board":
        ents = res.get("entries") or []
        claims = [f"{e.get('agent')}:{e.get('target') or _short(e.get('note'), 30)}" for e in ents]
        return f"{st} {len(ents)} entries [{' '.join(claims)}]"
    if tool == "read_inbox":
        msgs = res.get("messages") or []
        kinds = [f"{m.get('kind')}<{m.get('from')}" for m in msgs]
        return f"{st} {len(msgs)} messages [{' '.join(kinds)}]"
    if tool == "lookup":
        o = res.get("order") or {}
        return f"{st} " + _j({k: o.get(k) for k in ("customer", "amount", "issue", "days_since_order") if k in o}) \
            if o else _short(_j(res), 120)
    if tool == "verify":
        effs = res.get("effects") or []
        parts = []
        for e in effs:
            p = f"{e.get('agent')}:{e.get('intent')}={e.get('status')}@t{e.get('t')}"
            if e.get("amount") is not None:
                p += f"${e.get('amount')}"
            parts.append(p)
        return f"{st} target={res.get('target')} pass={res.get('pass')} pending={res.get('pending')} effects[{' '.join(parts)}]"
    # ids of the environment's records (delegation d001, report r001) are dropped: they are ledger ids
    rest = {k: v for k, v in res.items() if k not in ("status", "delegation", "report")}
    return f"{st}" + (f" {_short(_j(rest), 140)}" if rest else "")


def _fmt_call(call) -> str:
    if not isinstance(call, dict):
        return "MALFORMED " + _short(call, 100)
    tool = call.get("tool")
    args = call.get("args") if isinstance(call.get("args"), dict) else {}
    a = dict(args)
    if "body" in a:
        a["body"] = _short(a["body"], 50)
    if "brief" in a:
        a["brief"] = _short(a["brief"], 80)
    if "text" in a:
        a["text"] = _short(a["text"], 120)
    a.pop("task", None)   # the batch task id is the same on every call
    return f"{tool} {_j(a)}"


def build_view(events: list, view: str, task: dict | None = None) -> list:
    """Return a list of ``(priority, line)`` for the chosen view, in log order."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {VIEWS}")
    lines = [(PRI_KEEP, l) for l in _task_header(task)]
    for ev in events:
        kind, a, t, p = ev["event"], ev["agent"], ev["t"], ev["payload"]
        if kind == "llm_call":
            continue                                    # never: raw model I/O
        if kind == "report" and p.get("accepted") and ev.get("parent") is None:
            # the top agent's report goes to the owner (outside the tree): no inbox message is logged
            # for it, so the delivered report is taken from its report event (accepted = delivered)
            kind, p = "message", {"kind": "report", "from": a, "to": "owner", "status": p.get("status"),
                                  "claims": p.get("claims"), "text": p.get("text")}
        if kind == "message":
            mk = p.get("kind")
            if mk == "delegation":
                lines.append((PRI_KEEP, f"t{t} {p.get('from')} -> {p.get('to')} DELEGATE items={_j(p.get('item_ids'))} "
                                        f"brief=\"{_short(p.get('brief'), 80)}\""))
            elif mk == "report":
                claims = p.get("claims")
                cl = "none" if claims is None else " ".join(
                    f"{c.get('target')}:{c.get('intent')}" for c in claims if isinstance(c, dict))
                lines.append((PRI_KEEP, f"t{t} {p.get('from')} -> {p.get('to')} REPORT {p.get('status')} "
                                        f"claims=[{cl}] text=\"{_short(p.get('text'), 140)}\""))
            elif mk == "child_stopped":
                lines.append((PRI_KEEP, f"t{t} notice -> {p.get('to')}: {p.get('child')} stopped ({p.get('reason')})"))
            continue
        if kind == "report":
            continue        # the tool call/result covers it in full; delivered ones appear as messages
        if kind == "tool_call":
            call = p.get("call")
            tool = call.get("tool") if isinstance(call, dict) else None
            step = p.get("step")
            if tool in ("claim", "post_board"):
                args = call.get("args") if isinstance(call.get("args"), dict) else {}
                what = f"CLAIM {args.get('target')}" if tool == "claim" else f"NOTE \"{_short(args.get('note'), 100)}\""
                if view == "report":
                    lines.append((PRI_KEEP, f"t{t} {a} BOARD {what}"))
                    continue
            if view == "report" or tool == "delegate":
                continue                                # a delegation already shows as its DELEGATE line
            pri = PRI_KEEP if tool in SIDE_EFFECT_TOOLS or tool in COORD_TOOLS else \
                PRI_VERIFY if tool == "verify" else PRI_READ_CALL
            lines.append((pri, f"t{t} {a}#{step} CALL {_fmt_call(call)}"))
            continue
        if kind == "tool_result":
            if view == "report":
                continue
            tool = p.get("tool")
            pri = PRI_KEEP if tool in SIDE_EFFECT_TOOLS or tool in COORD_TOOLS else \
                PRI_VERIFY if tool == "verify" else PRI_READ_RESULT
            lines.append((pri, f"t{t} {a}#{p.get('step')}   => {_fmt_result(tool, p.get('result'))}"))
    return lines


def trim(lines: list, budget_tokens: int) -> tuple:
    """Deterministically fit ``lines`` into ``budget_tokens`` (estimate over the joined text).

    Order: drop priority-0 lines (read results), then priority-1 (read calls), then
    priority-2 (verify), oldest first within a priority; if still over, shorten the
    remaining lines to 160, then 100 characters; if still over, keep the head and tail
    and drop the middle with a marker. Returns ``(kept_lines, info)``."""
    info = {"lines_in": len(lines), "dropped": {}, "shortened": 0, "middle_cut": 0}
    text_of = lambda ls: "\n".join(l for _, l in ls)
    cur = list(lines)
    if est_tokens(text_of(cur)) <= budget_tokens:
        info["lines_out"] = len(cur)
        return [l for _, l in cur], info
    for pri in (PRI_READ_RESULT, PRI_READ_CALL, PRI_VERIFY):
        idx = [i for i, (p, _) in enumerate(cur) if p == pri]
        drop = set()
        total = est_tokens(text_of(cur))
        for i in idx:
            if total <= budget_tokens:
                break
            drop.add(i)
            total -= est_tokens(cur[i][1] + "\n")
        if drop:
            info["dropped"][str(pri)] = len(drop)
            cur = [x for i, x in enumerate(cur) if i not in drop]
        if est_tokens(text_of(cur)) <= budget_tokens:
            info["lines_out"] = len(cur)
            return [l for _, l in cur], info
    for width in (160, 100):
        new = [(p, l if len(l) <= width else l[: width - 3] + "...") for p, l in cur]
        info["shortened"] += sum(1 for (a, b) in zip(cur, new) if a[1] != b[1])
        cur = new
        if est_tokens(text_of(cur)) <= budget_tokens:
            info["lines_out"] = len(cur)
            return [l for _, l in cur], info
    # head + tail
    head, tail = 1, len(cur) - 1
    keep_h, keep_t = [cur[0]], []
    used = est_tokens(cur[0][1]) + 20
    while head <= tail:
        for side in ("t", "h"):
            if head > tail:
                break
            l = cur[tail][1] if side == "t" else cur[head][1]
            c = est_tokens(l + "\n")
            if used + c > budget_tokens:
                head = tail + 1
                break
            used += c
            if side == "t":
                keep_t.insert(0, cur[tail])
                tail -= 1
            else:
                keep_h.append(cur[head])
                head += 1
    cut = len(cur) - len(keep_h) - len(keep_t)
    info["middle_cut"] = cut
    out = [l for _, l in keep_h] + ([f"... [{cut} lines omitted to fit the budget] ..."] if cut else []) + \
          [l for _, l in keep_t]
    info["lines_out"] = len(out)
    return out, info


def render(ep_dir, view: str, budget_tokens: int = 8000) -> tuple:
    """Episode directory -> (view text, trim info)."""
    lines = build_view(load_events(Path(ep_dir)), view, task_input(Path(ep_dir)))
    kept, info = trim(lines, budget_tokens)
    text = "\n".join(kept)
    info["est_tokens"] = est_tokens(text)
    info["view"] = view
    return text, info
