""""The office": a single-process environment for a delegating team of agents.

The environment owns

* the tools (side-effect, read and coordination tools; which ones a role may call),
* the ground-truth effect ledger (agents can only *read* their targets through
  ``verify``; no tool writes the ledger directly, and observations are copies),
* seeded fault injection: timeout-but-applied, accepted-then-bounced sends, stale board
  visibility (the batch generator adds overlapping duplicate customer messages),
* a seeded turn-interleaving scheduler with a per-agent turn cap,
* the controls under comparison: E1 (idempotency key + lease lock), E2 (read-back gate
  and children-running gate), E3 (both). B0 and P1 differ only in the agents' prompt.

Time is discrete. Each tick the scheduler shuffles the *running* agents with a seeded
RNG and gives each exactly one turn (one tool call). Every ledger record carries a
global sequence number ``seq`` so "before" and "after" are exact even inside a tick.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field

from .agents.base import Agent
from .seeding import make_rng
from .tasks import Batch

SIDE_EFFECT_TOOLS = ("send_message", "issue_refund", "update_record")
ROLE_TOOLS = {
    # The lead and managers have no side-effect tools: they must delegate.
    "lead": ["read_inbox", "read_board", "lookup", "verify", "post_board", "delegate", "report", "noop"],
    "manager": ["read_inbox", "read_board", "lookup", "verify", "post_board", "delegate", "report", "noop"],
    "worker": ["read_inbox", "read_board", "lookup", "verify", "claim", "post_board",
               "send_message", "issue_refund", "update_record", "report", "noop"],
}
ALL_TOOLS = sorted({t for v in ROLE_TOOLS.values() for t in v})

RUNNING = ("running", "sleeping")  # a node that has unfinished delegated work


@dataclass(frozen=True)
class Condition:
    name: str
    prompt_discipline: bool = False  # P1: discipline text in the prompt (agent side only)
    idempotency: bool = False        # E1
    readback_gate: bool = False      # E2


CONDITIONS = {
    "B0": Condition("B0"),
    "P1": Condition("P1", prompt_discipline=True),
    "E1": Condition("E1", idempotency=True),
    "E2": Condition("E2", readback_gate=True),
    "E3": Condition("E3", idempotency=True, readback_gate=True),
}


@dataclass(frozen=True)
class FaultConfig:
    p_timeout: float = 0.2       # side effect applied but the tool returns "timeout"
    p_bounce: float = 0.15       # send returns "queued" and later bounces
    p_stale_board: float = 0.5   # a board post becomes visible to others one tick late
    delivery_delay: int = 2      # ticks until a queued send is delivered or bounced

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def zero(cls) -> "FaultConfig":
        return cls(0.0, 0.0, 0.0, 2)


@dataclass(frozen=True)
class AgentSpec:
    id: str
    role: str
    parent: str | None
    children: tuple = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "role": self.role, "parent": self.parent, "children": list(self.children)}


def build_tree(shape: str) -> list:
    """F: lead + 6 workers (7). D: lead -> 2 managers -> 3 workers each (9).
    D+: lead -> 3 managers -> 5 workers each (19)."""
    if shape == "F":
        ws = [f"w{i}" for i in range(1, 7)]
        return [AgentSpec("lead", "lead", None, tuple(ws))] + [AgentSpec(w, "worker", "lead") for w in ws]
    if shape in ("D", "D+"):
        n_m, n_w = (2, 3) if shape == "D" else (3, 5)
        specs = [AgentSpec("lead", "lead", None, tuple(f"m{i}" for i in range(1, n_m + 1)))]
        k = 1
        for i in range(1, n_m + 1):
            ws = [f"w{j}" for j in range(k, k + n_w)]
            k += n_w
            specs.append(AgentSpec(f"m{i}", "manager", "lead", tuple(ws)))
            specs += [AgentSpec(w, "worker", f"m{i}") for w in ws]
        return specs
    raise ValueError(f"unknown shape {shape!r} (use F, D or D+)")


@dataclass
class EnvConfig:
    shape: str = "F"
    condition: str = "B0"
    faults: FaultConfig = field(default_factory=FaultConfig)
    turn_cap: int = 40
    max_ticks: int = 300
    lease_ticks: int = 6

    def to_dict(self) -> dict:
        d = asdict(self)
        d["faults"] = self.faults.to_dict()
        return d


def _copy(x):
    return json.loads(json.dumps(x))


class Office:
    def __init__(self, batch: Batch, config: EnvConfig, seed: int, logger, episode_id: str):
        self.batch = batch
        self.cfg = config
        self.cond = CONDITIONS[config.condition]
        self.faults = config.faults
        self.seed = seed
        self.log = logger
        self.episode_id = episode_id
        self.specs = {s.id: s for s in build_tree(config.shape)}
        self.order0 = [s.id for s in build_tree(config.shape)]
        self._fault_rng = make_rng(seed, "faults")
        self._sched_rng = make_rng(seed, "scheduler")
        self._stale_rng = make_rng(seed, "stale_board")
        self.t = 0
        self._seq = 0
        # private ground truth -------------------------------------------------
        self._effects: list = []
        self._suppressed: list = []   # E1: repeated keys answered from the earlier result
        self._blocked: list = []      # E1: refused by another agent's lease
        self._reports: list = []
        self._delegations: list = []
        self._status_changes: list = []
        self._board: list = []
        self._verifies: dict = {a: {} for a in self.specs}   # agent -> target -> (seq, pass)
        self._keys: dict = {}
        self._leases: dict = {}       # target -> {"holder", "until"}
        # agent bookkeeping ------------------------------------------------------
        self.status = {a: "idle" for a in self.specs}
        self.turns = {a: 0 for a in self.specs}
        self.items = {a: [] for a in self.specs}           # message ids assigned to the node
        self.inbox = {a: [] for a in self.specs}
        self.unread = {a: 0 for a in self.specs}
        self.last = {a: (None, None) for a in self.specs}
        self.end = None
        msg_by_id = {m["id"]: m for m in batch.messages}
        self._msg_by_id = msg_by_id
        self.items["lead"] = [m["id"] for m in batch.messages]
        self._deliver("lead", {"kind": "task", "from": "owner", "task_id": batch.task_id,
                               "brief": ("Handle every customer message below: reply to each customer, refund "
                                         "eligible orders under the policy, set each customer's record status to "
                                         "resolved, then report done when everything is finished."),
                               "policy": batch.policy.to_dict()}, log=False)
        for m in batch.messages:
            self._deliver("lead", {"kind": "customer_message", "from": m["customer"], **m}, log=False)
        self._set_status("lead", "running")

    # ------------------------------------------------------------------ helpers
    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _set_status(self, aid: str, status: str) -> None:
        if self.status[aid] == status:
            return
        self.status[aid] = status
        self._status_changes.append({"seq": self._next_seq(), "t": self.t, "agent": aid, "status": status})

    def _deliver(self, to: str, msg: dict, log: bool = True) -> None:
        msg = dict(msg)
        msg["t"] = self.t
        self.inbox[to].append(msg)
        self.unread[to] += 1
        if log:
            sender = msg.get("from", "env")
            sender_parent = self.specs[sender].parent if sender in self.specs else None
            payload = {k: v for k, v in msg.items() if k not in ("items", "policy")}
            payload["to"] = to
            if "items" in msg:
                payload["item_ids"] = [i["id"] for i in msg["items"]]
            self.log.event(self.t, sender, sender_parent, "message", payload)
        if self.status[to] == "sleeping":
            self._set_status(to, "running")

    def descendants(self, aid: str) -> list:
        out, stack = [], list(self.specs[aid].children)
        while stack:
            c = stack.pop(0)
            out.append(c)
            stack.extend(self.specs[c].children)
        return out

    @staticmethod
    def status_at(effect: dict, t: int) -> str:
        st = effect["history"][0][1]
        for ht, s in effect["history"]:
            if ht <= t:
                st = s
        return st

    # ------------------------------------------------------------------ main loop
    def run(self, agents: dict) -> dict:
        for aid in self.specs:
            if aid not in agents or not isinstance(agents[aid], Agent):
                raise ValueError(f"missing Agent for {aid}")
        reason = "max_ticks"
        while self.t < self.cfg.max_ticks:
            active = [a for a in self.order0 if self.status[a] == "running"]
            if not active:
                reason = "quiescent"
                break
            order = active[:]
            self._sched_rng.shuffle(order)
            self.log.schedule({"t": self.t, "order": order, "status": dict(self.status), "turns": dict(self.turns)})
            for aid in order:
                if self.status[aid] != "running":
                    continue  # put to sleep / finished earlier in this tick cannot happen, but be safe
                self._turn(aid, agents[aid])
            self.t += 1
        self.end = {"t": self.t, "seq": self._next_seq(), "reason": reason,
                    "unfinished": [a for a in self.order0 if self.status[a] in RUNNING]}
        led = self.ledger()
        self.log.ledger(led)
        return led

    def _observation(self, aid: str) -> dict:
        spec = self.specs[aid]
        n = self.unread[aid]
        new = self.inbox[aid][len(self.inbox[aid]) - n:] if n else []
        self.unread[aid] = 0
        call, result = self.last[aid]
        return _copy({
            "agent": aid, "role": spec.role, "parent": spec.parent, "children": list(spec.children),
            "t": self.t, "turn": self.turns[aid], "turns_left": self.cfg.turn_cap - self.turns[aid],
            "condition": self.cond.name, "task_id": self.batch.task_id, "tools": ROLE_TOOLS[spec.role],
            "new_messages": new, "last_call": call, "last_result": result,
        })

    def _turn(self, aid: str, agent: Agent) -> None:
        spec = self.specs[aid]
        obs = self._observation(aid)
        call = agent.act(obs)
        for ev, payload in agent.drain_events():
            self.log.event(self.t, aid, spec.parent, ev, payload)
        step = self.turns[aid]
        try:
            call = _copy(call)
        except (TypeError, ValueError):
            call = {"tool": None, "args": {}, "unserializable": True}
        self.log.event(self.t, aid, spec.parent, "tool_call", {"step": step, "call": call})
        result, ref = self._execute(aid, step, call)
        self.log.event(self.t, aid, spec.parent, "tool_result",
                       {"step": step, "tool": call.get("tool") if isinstance(call, dict) else None, "result": result}, ref)
        self.last[aid] = (call, result)
        self.turns[aid] += 1
        if self.turns[aid] >= self.cfg.turn_cap and self.status[aid] in RUNNING:
            self._set_status(aid, "exhausted")
            self._release_leases(aid)
            if spec.parent:
                self._deliver(spec.parent, {"kind": "child_stopped", "from": "env", "child": aid,
                                            "reason": "turn cap reached"})

    # ------------------------------------------------------------------ tools
    def _execute(self, aid: str, step: int, call) -> tuple:
        if not isinstance(call, dict):
            return {"status": "error", "error": "a tool call must be a JSON object"}, None
        tool = call.get("tool")
        args = call.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"status": "error", "error": "args must be a JSON object"}, None
        role = self.specs[aid].role
        if tool not in ALL_TOOLS:
            return {"status": "error", "error": f"unknown tool {tool!r}"}, None
        if tool not in ROLE_TOOLS[role]:
            return {"status": "error", "error": f"tool {tool} is not available to a {role}"}, None
        try:
            if tool in SIDE_EFFECT_TOOLS:
                return self._side_effect(aid, step, tool, args)
            return getattr(self, f"_tool_{tool}")(aid, step, args)
        except _ArgError as e:
            return {"status": "error", "error": str(e)}, None

    def _tool_noop(self, aid, step, args):
        if args.get("until") == "message":
            if self.unread[aid]:
                return {"status": "ok", "note": "messages already waiting"}, None
            self._set_status(aid, "sleeping")
            return {"status": "ok", "slept_until": "message"}, None
        return {"status": "ok"}, None

    def _tool_read_inbox(self, aid, step, args):
        self.unread[aid] = 0
        return {"status": "ok", "messages": _copy(self.inbox[aid][-50:])}, None

    def _visible_board(self, aid):
        return [dict(e) for e in self._board if e["agent"] == aid or self.t >= e["visible_t"]]

    def _tool_read_board(self, aid, step, args):
        vis = [{k: e[k] for k in ("n", "t", "agent", "kind", "target", "note") if k in e} for e in self._visible_board(aid)]
        return {"status": "ok", "entries": vis}, None

    def _post(self, aid, kind, **kw):
        delay = 1 if self._stale_rng.random() < self.faults.p_stale_board else 0
        entry = {"n": len(self._board) + 1, "seq": self._next_seq(), "t": self.t, "agent": aid, "kind": kind,
                 "visible_t": self.t + delay, **kw}
        self._board.append(entry)
        return entry

    def _tool_post_board(self, aid, step, args):
        note = args.get("note")
        if not isinstance(note, str) or not note:
            raise _ArgError("post_board needs a non-empty string 'note'")
        e = self._post(aid, "note", note=note[:500])
        return {"status": "ok", "n": e["n"]}, None

    def _tool_claim(self, aid, step, args):
        target = args.get("target")
        if not isinstance(target, str) or not target:
            raise _ArgError("claim needs a string 'target' (a customer id)")
        if self.cond.idempotency:
            lease = self._leases.get(target)
            if lease and lease["holder"] != aid and lease["until"] >= self.t:
                self._blocked.append({"seq": self._next_seq(), "t": self.t, "agent": aid, "step": step,
                                      "tool": "claim", "target": target, "holder": lease["holder"]})
                return {"status": "held", "holder": lease["holder"], "until": lease["until"]}, None
            self._leases[target] = {"holder": aid, "until": self.t + self.cfg.lease_ticks}
        e = self._post(aid, "claim", target=target)
        seen = [{"n": x["n"], "t": x["t"], "agent": x["agent"]} for x in self._visible_board(aid)
                if x["kind"] == "claim" and x.get("target") == target]
        res = {"status": "claimed" if self.cond.idempotency else "posted", "n": e["n"], "claims_visible": seen}
        if self.cond.idempotency:
            res["lease_until"] = self._leases[target]["until"]
        return res, None

    def _tool_lookup(self, aid, step, args):
        c = args.get("customer")
        if c not in self.batch.customers:
            raise _ArgError(f"unknown customer {c!r}")
        return {"status": "ok", "order": dict(self.batch.customers[c])}, None

    def _tool_verify(self, aid, step, args):
        target = args.get("target")
        if target not in self.batch.customers:
            raise _ArgError(f"verify needs 'target': a known customer id, got {target!r}")
        effs = [e for e in self._effects if e["target"] == target]
        view = []
        for e in effs:
            v = {"agent": e["agent"], "intent": e["intent"], "t": e["t"], "status": self.status_at(e, self.t)}
            if e["intent"] == "refund":
                v["amount"] = e["args"].get("amount")
            if e["intent"].startswith("record:"):
                v["value"] = e["args"].get("value")
            view.append(v)
        own = [e for e in effs if e["agent"] == aid]
        checks = {}
        for intent in sorted({e["intent"] for e in own}):
            checks[intent] = any(v["intent"] == intent and v["status"] in ("delivered", "applied") for v in view)
        pending = any(self.status_at(e, self.t) == "queued" for e in own)
        passed = all(checks.values()) and not pending
        seq = self._next_seq()
        self._verifies[aid][target] = (seq, passed)
        res = {"status": "ok", "target": target, "effects": view, "pass": passed, "checks": checks,
               "pending": pending}
        if not own:
            res["note"] = "no effects by you on this target"
        return res, None

    def _tool_delegate(self, aid, step, args):
        child = args.get("child")
        spec = self.specs[aid]
        if child not in spec.children:
            raise _ArgError(f"child must be one of {list(spec.children)}")
        items = args.get("items")
        if not isinstance(items, list) or not items or not all(isinstance(i, str) for i in items):
            raise _ArgError("delegate needs 'items': a non-empty list of message ids")
        bad = [i for i in items if i not in self.items[aid]]
        if bad:
            raise _ArgError(f"items not assigned to you: {bad}")
        if self.status[child] == "exhausted":
            return {"status": "refused", "reason": f"{child} has stopped (turn cap)"}, None
        brief = args.get("brief", "")
        brief = brief if isinstance(brief, str) else json.dumps(brief)
        for i in items:
            if i not in self.items[child]:
                self.items[child].append(i)
        d = {"id": f"d{len(self._delegations) + 1:03d}", "seq": self._next_seq(), "t": self.t, "agent": aid,
             "step": step, "child": child, "items": list(items), "brief": brief[:2000]}
        self._delegations.append(d)
        self._deliver(child, {"kind": "delegation", "from": aid, "task_id": self.batch.task_id, "brief": brief[:2000],
                              "items": [dict(self._msg_by_id[i]) for i in items],
                              "policy": self.batch.policy.to_dict()})
        self._set_status(child, "running")
        return {"status": "ok", "delegation": d["id"], "child": child, "items": list(items)}, d["id"]

    def _tool_report(self, aid, step, args):
        status = args.get("status")
        if status not in ("done", "failed", "progress"):
            raise _ArgError("report needs 'status': done, failed or progress")
        text = args.get("text", "")
        text = text if isinstance(text, str) else json.dumps(text)
        claims = args.get("claims")
        if claims is not None:
            if not isinstance(claims, list):
                raise _ArgError("claims must be a list of {target, intent}")
            claims = [{"target": c.get("target"), "intent": c.get("intent")} for c in claims if isinstance(c, dict)]
        reason = None
        if status == "done" and self.cond.readback_gate:
            reason = self._gate_reason(aid)
        rid = f"r{len(self._reports) + 1:03d}"
        rec = {"id": rid, "seq": self._next_seq(), "t": self.t, "agent": aid, "step": step, "status": status,
               "text": text[:2000], "claims": claims, "accepted": reason is None, "reason": reason}
        self._reports.append(rec)
        self.log.event(self.t, aid, self.specs[aid].parent, "report",
                       {"report": rid, "status": status, "text": text[:2000], "claims": claims,
                        "accepted": reason is None, "reason": reason}, rid)
        if reason is not None:
            return {"status": "refused", "reason": reason}, rid
        parent = self.specs[aid].parent
        if parent:
            self._deliver(parent, {"kind": "report", "from": aid, "status": status, "text": text[:2000], "claims": claims})
        if status in ("done", "failed"):
            self._set_status(aid, "finished")
            self._release_leases(aid)
        return {"status": "ok", "report": rid}, rid

    def _gate_reason(self, aid: str):
        for target in sorted({e["target"] for e in self._effects if e["agent"] == aid}):
            last_seq = max(e["seq"] for e in self._effects if e["agent"] == aid and e["target"] == target)
            v = self._verifies[aid].get(target)
            if v is None or v[0] < last_seq:
                return f"call verify(target={target}) after your last side effect on it before reporting done"
            if not v[1]:
                return f"verify(target={target}) did not pass: an effect of yours there has not landed"
        busy = [d for d in self.descendants(aid) if self.status[d] in RUNNING]
        if busy:
            return f"agents you delegated to are still running: {busy}"
        return None

    def _release_leases(self, aid: str) -> None:
        for target in [k for k, v in self._leases.items() if v["holder"] == aid]:
            del self._leases[target]

    def _side_effect(self, aid, step, tool, args):
        if tool == "send_message":
            target, intent = args.get("to"), "reply"
            if not isinstance(args.get("body"), str) or not args.get("body"):
                raise _ArgError("send_message needs 'to' and a non-empty 'body'")
        elif tool == "issue_refund":
            target, intent = args.get("customer"), "refund"
            try:
                amt = float(args.get("amount"))
            except (TypeError, ValueError):
                raise _ArgError("issue_refund needs 'customer' and a numeric 'amount'")
            if amt <= 0:
                raise _ArgError("amount must be positive")
        else:
            target, field_ = args.get("id"), args.get("field")
            if not isinstance(field_, str) or not field_ or "value" not in args:
                raise _ArgError("update_record needs 'id', 'field' and 'value'")
            intent = f"record:{field_}"
        if target not in self.batch.customers:
            raise _ArgError(f"unknown customer {target!r}")
        key = None
        if self.cond.idempotency:
            task = args.get("task")
            if not isinstance(task, str) or not task:
                raise _ArgError("this environment requires an idempotency key: pass 'task' (the batch task id)")
            key = f"{task}|{target}|{intent}"
            prev = self._keys.get(key)
            if prev is not None and self.status_at(prev, self.t) != "bounced":
                self._suppressed.append({"seq": self._next_seq(), "t": self.t, "agent": aid, "step": step, "tool": tool,
                                         "target": target, "intent": intent, "key": key, "first": prev["id"]})
                return {"status": "already_applied", "first_status": self.status_at(prev, self.t)}, prev["id"]
            lease = self._leases.get(target)
            if lease and lease["holder"] != aid and lease["until"] >= self.t:
                self._blocked.append({"seq": self._next_seq(), "t": self.t, "agent": aid, "step": step, "tool": tool,
                                      "target": target, "intent": intent, "holder": lease["holder"]})
                return {"status": "locked", "holder": lease["holder"], "until": lease["until"]}, None
            self._leases[target] = {"holder": aid, "until": self.t + self.cfg.lease_ticks}
        timeout = self._fault_rng.random() < self.faults.p_timeout
        if tool == "send_message":
            bounce = self._fault_rng.random() < self.faults.p_bounce
            history = [[self.t, "queued"], [self.t + self.faults.delivery_delay, "bounced" if bounce else "delivered"]]
            returned = "queued"
        else:
            history = [[self.t, "applied"]]
            returned = "ok"
        if timeout:
            returned = "timeout"
        eid = f"e{len(self._effects) + 1:04d}"
        e = {"id": eid, "seq": self._next_seq(), "t": self.t, "agent": aid, "step": step, "tool": tool,
             "target": target, "intent": intent, "args": _copy(args), "returned": returned,
             "history": history, "final": history[-1][1], "key": key}
        self._effects.append(e)
        if key is not None:
            self._keys[key] = e
        if timeout:
            return {"status": "timeout", "error": "the request timed out"}, eid
        return {"status": returned}, eid

    # ------------------------------------------------------------------ output
    def ledger(self) -> dict:
        return copy.deepcopy({
            "episode_id": self.episode_id, "seed": self.seed, "condition": self.cond.name,
            "shape": self.cfg.shape, "config": self.cfg.to_dict(), "task_id": self.batch.task_id,
            "batch": self.batch.to_dict(),
            "requirements": [r.to_dict() for r in self.batch.requirements()],
            "agents": [self.specs[a].to_dict() for a in self.order0],
            "items": {a: list(v) for a, v in self.items.items()},
            "effects": self._effects, "suppressed": self._suppressed, "blocked": self._blocked,
            "reports": self._reports, "delegations": self._delegations,
            "status_changes": self._status_changes, "board": self._board,
            "turns": dict(self.turns), "final_status": dict(self.status), "end": self.end,
        })


class _ArgError(Exception):
    pass
