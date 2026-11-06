"""Scripted (no-LLM) reference policies with known failure behavior.

``naive``  (the "naive retrier")
    Lead/manager: split the items round-robin over its children by message, delegate,
    then report done right away, claiming everything. Worker: handles every assigned
    message on its own (no board, no dedup across messages, no verify); after a
    ``timeout`` (or ``locked``) result it simply calls the same tool again, up to
    ``max_retries`` times; treats ``queued`` as success. Expected failures: duplicates
    from retries after timeout-but-applied and from overlapping messages, false dones
    from bounced sends, premature lead/manager dones.

``careful``
    Lead/manager: delegate, wait for every child to report (or stop), verify every
    customer itself, then report done only if all requirements hold (else failed).
    Worker: dedups its customers, claims each on the board and backs off if an earlier
    claim exists (a claim protocol that tolerates one tick of board staleness),
    verifies before acting and after any timeout instead of retrying blindly, waits
    for queued sends to resolve and resends a bounced one, waits for customers another
    worker owns, and reports done only when its verify shows every requirement met.

Both policies are deterministic given their observations, so an episode with scripted
agents is a pure function of the seed. Under refusals from the E2 gate they comply
mechanically (naive: verify each target it touched, then report again).
"""
from __future__ import annotations

from .base import NOOP, Agent

RETRY_STATUSES = ("timeout", "locked")
OK_STATUSES = ("ok", "queued", "already_applied")


def _call(tool, **args):
    return {"tool": tool, "args": args}


def _eligible(order: dict, policy: dict) -> bool:
    return (order["issue"] in policy["eligible_issues"] and int(order["days_since_order"]) <= int(policy["max_days"])
            and int(order["amount"]) <= int(policy["max_amount"]))


def _reply_body(order: dict, eligible: bool) -> str:
    if eligible:
        return (f"Hi {order['name']}, sorry about order {order['order_id']}. We have issued a full refund of "
                f"${order['amount']}.")
    return (f"Hi {order['name']}, thanks for writing about order {order['order_id']}. Under our policy this order "
            f"is not eligible for a refund, but we have noted your feedback.")


def split_round_robin(items: list, children: list) -> dict:
    out = {c: [] for c in children}
    for i, it in enumerate(items):
        out[children[i % len(children)]].append(it)
    return out


class ScriptedAgent(Agent):
    """Generator-driven policy: ``policy()`` yields tool calls and receives observations."""

    def __init__(self, agent_id: str, role: str, max_retries: int = 3, wait_turns: int = 12):
        self.agent_id = agent_id
        self.role = role
        self.max_retries = max_retries
        self.wait_turns = wait_turns
        self.messages: list = []
        self.items: list = []      # customer-message dicts assigned to this agent
        self.task_id = None
        self.policy_rules = None
        self.children: list = []
        self.obs = None
        self._gen = None
        self._done = False

    def _absorb(self, obs: dict) -> None:
        self.obs = obs
        self.children = obs.get("children", [])
        self.task_id = obs.get("task_id", self.task_id)
        for m in obs.get("new_messages", []):
            self.messages.append(m)
            if m.get("kind") == "customer_message":
                self.items.append({"id": m["id"], "customer": m["customer"], "text": m["text"]})
            elif m.get("kind") == "delegation":
                self.items.extend(m.get("items", []))
            if m.get("policy"):
                self.policy_rules = m["policy"]

    def act(self, observation: dict) -> dict:
        self._absorb(observation)
        if self._done:
            return _call("noop", until="message")
        try:
            if self._gen is None:
                self._gen = self.policy()
                return next(self._gen)
            return self._gen.send(observation)
        except StopIteration:
            self._done = True
            return _call("noop", until="message")

    def result(self) -> dict:
        return (self.obs or {}).get("last_result") or {}

    def policy(self):  # pragma: no cover - abstract
        yield NOOP

    # shared pieces -------------------------------------------------------------
    def wait_for_items(self):
        while not self.items:
            yield _call("noop", until="message")

    def wait_unless_news(self):
        """Sleep until a message arrives, unless one arrived with the current observation."""
        if not (self.obs or {}).get("new_messages"):
            yield _call("noop", until="message")

    def children_settled(self, children: list) -> bool:
        settled = set()
        for m in self.messages:
            if m.get("kind") == "report" and m.get("status") in ("done", "failed"):
                settled.add(m.get("from"))
            if m.get("kind") == "child_stopped":
                settled.add(m.get("child"))
        return all(c in settled for c in children)

    def delegate_round_robin(self):
        ids = [m["id"] for m in self.items]
        plan = split_round_robin(ids, list(self.children))
        used = []
        for child, its in plan.items():
            if its:
                yield _call("delegate", child=child, items=its,
                            brief="Handle these customer messages: reply to each customer, refund eligible orders "
                                  "under the policy, set the record status to resolved, then report.")
                used.append(child)
        return used


class NaiveAgent(ScriptedAgent):
    def policy(self):
        yield from self.wait_for_items()
        if self.role in ("lead", "manager"):
            yield from self.delegate_round_robin()
            claims = self._all_claims()
            while True:
                yield _call("report", status="done", text="All delegated; done.", claims=claims)
                if self.result().get("status") == "ok":
                    return
                yield from self.wait_unless_news()
        # worker
        touched, claims = [], []
        i = 0
        while i < len(self.items):
            m = self.items[i]
            i += 1
            c = m["customer"]
            yield _call("lookup", customer=c)
            order = self.result().get("order")
            if not order:
                continue
            elig = _eligible(order, self.policy_rules)
            yield from self._retry(_call("send_message", to=c, body=_reply_body(order, elig), task=self.task_id))
            if elig:
                yield from self._retry(_call("issue_refund", customer=c, amount=order["amount"], task=self.task_id))
            yield from self._retry(_call("update_record", id=c, field="status", value="resolved", task=self.task_id))
            if c not in touched:
                touched.append(c)
                claims += [{"target": c, "intent": k} for k in ("reply", "record:status") + (("refund",) if elig else ())]
        while True:
            yield _call("report", status="done", text=f"Handled {len(self.items)} messages.", claims=claims)
            if self.result().get("status") == "ok":
                return
            for c in touched:
                yield _call("verify", target=c)

    def _retry(self, call):
        for _ in range(self.max_retries + 1):
            yield call
            if self.result().get("status") not in RETRY_STATUSES:
                return

    def _all_claims(self):
        cs = []
        for m in self.items:
            if m["customer"] not in cs:
                cs.append(m["customer"])
        return [{"target": c, "intent": k} for c in cs for k in ("reply", "record:status")]


class CarefulAgent(ScriptedAgent):
    def policy(self):
        yield from self.wait_for_items()
        if self.role in ("lead", "manager"):
            used = yield from self.delegate_round_robin()
            while not self.children_settled(used):
                yield _call("noop", until="message")
            ok, claims = yield from self._check_all(self._customers())
            while True:
                yield _call("report", status="done" if ok else "failed",
                            text="Verified every customer." if ok else "Some customers are not finished.",
                            claims=claims)
                if self.result().get("status") == "ok":
                    return
                yield from self.wait_unless_news()
        # worker ---------------------------------------------------------------
        customers = self._customers()
        mine, others, leased = [], [], set()
        for c in customers:
            yield _call("claim", target=c)
            st = self.result().get("status")
            if st == "held":
                others.append(c)
                continue
            if st == "claimed":  # E1: the environment granted an exclusive lease
                leased.add(c)
            mine.append(c)
        # Confirm board claims one tick later, when every competing earlier claim is visible
        # (board staleness is at most one tick). The earliest claim (lowest n) owns the customer.
        yield _call("read_board")
        entries = self.result().get("entries", [])
        for c in list(mine):
            if c in leased:
                continue
            cl = [e for e in entries if e.get("kind") == "claim" and e.get("target") == c]
            if cl and min(cl, key=lambda e: e["n"])["agent"] != self.agent_id:
                mine.remove(c)
                others.append(c)
        for c in mine:
            yield _call("lookup", customer=c)
            order = self.result()["order"]
            elig = _eligible(order, self.policy_rules)
            yield _call("verify", target=c)
            state = self.result()
            needs = ["reply", "record:status"] + (["refund"] if elig else [])
            locked = False
            for intent in needs:
                if locked or _present(state, intent, order):
                    continue
                for _ in range(self.max_retries + 1):
                    yield self._effect_call(intent, c, order, elig)
                    st = self.result().get("status")
                    if st == "locked":
                        locked = True
                        break
                    if st != "timeout":
                        break
                    # ambiguous result: read back instead of retrying blindly
                    yield _call("verify", target=c)
                    state = self.result()
                    if _present(state, intent, order):
                        break
            if locked:  # another agent holds the lease: let it finish, check later
                others.append(c)
                continue
            # read back: wait for the reply to resolve, resend after a bounce
            for _ in range(self.wait_turns):
                yield _call("verify", target=c)
                state = self.result()
                if _satisfied(state, "reply", order) and not state.get("pending"):
                    break
                if _pending(state, "reply"):
                    yield _call("noop")
                    continue
                yield self._effect_call("reply", c, order, elig)
                if self.result().get("status") == "locked":
                    break
        ok, claims = yield from self._check_all(customers)
        while True:
            yield _call("report", status="done" if ok else "failed",
                        text="Verified." if ok else "Not all requirements could be confirmed.", claims=claims)
            if self.result().get("status") == "ok":
                return
            for c in customers:
                yield _call("verify", target=c)

    def _customers(self):
        cs = []
        for m in self.items:
            if m["customer"] not in cs:
                cs.append(m["customer"])
        return cs

    def _effect_call(self, intent, c, order, elig):
        if intent == "reply":
            return _call("send_message", to=c, body=_reply_body(order, elig), task=self.task_id)
        if intent == "refund":
            return _call("issue_refund", customer=c, amount=order["amount"], task=self.task_id)
        return _call("update_record", id=c, field="status", value="resolved", task=self.task_id)

    def _check_all(self, customers):
        """Verify every customer (waiting a bounded number of turns for ones still in progress)."""
        ok_all, claims = True, []
        for c in customers:
            yield _call("lookup", customer=c)
            order = self.result()["order"]
            elig = _eligible(order, self.policy_rules)
            needs = ["reply", "record:status"] + (["refund"] if elig else [])
            good = False
            for _ in range(self.wait_turns):
                yield _call("verify", target=c)
                state = self.result()
                good = all(_satisfied(state, k, order) for k in needs) and not (
                    not elig and any(e["intent"] == "refund" for e in state.get("effects", [])))
                # a worker waits for a customer another worker owns; a lead/manager (whose
                # children have all settled) only waits for sends still in flight
                if good or (self.role != "worker" and not _anyone_pending(state)):
                    break
                yield _call("noop")
            if good:
                claims += [{"target": c, "intent": k} for k in needs]
            else:
                ok_all = False
        return ok_all, claims


def _present(state, intent, order):
    """Effect landed or is on its way (queued) - do not act again."""
    return _satisfied(state, intent, order) or _pending(state, intent)


def _satisfied(state, intent, order):
    for e in state.get("effects", []):
        if e["intent"] != intent or e["status"] not in ("delivered", "applied"):
            continue
        if intent == "refund" and int(round(float(e.get("amount", 0)))) != int(order["amount"]):
            continue
        if intent == "record:status" and e.get("value") != "resolved":
            continue
        return True
    return False


def _pending(state, intent):
    return any(e["intent"] == intent and e["status"] == "queued" for e in state.get("effects", []))


def _anyone_pending(state):
    return any(e["status"] == "queued" for e in state.get("effects", []))


POLICIES = {"naive": NaiveAgent, "careful": CarefulAgent}


def make_scripted_agents(policy: str, specs: list, **kw) -> dict:
    cls = POLICIES[policy]
    return {s.id: cls(s.id, s.role, **kw) for s in specs}
