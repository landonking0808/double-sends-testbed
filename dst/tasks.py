"""Seeded synthetic task batches ("the office" inbox) and machine-checkable requirements.

A batch is a set of customer messages about orders. The team must, for every
customer who wrote (once or twice):

* ``reply``         - at least one message to the customer that was delivered (not bounced);
* ``record:status`` - the customer's record has ``status`` set to ``resolved``;
* ``refund``        - if the order is eligible under the policy: a refund of exactly the
                      order amount was applied;
* ``no_refund``     - if the order is not eligible: no refund was applied.

Some customers write twice (``n_repeat``), so a naive split of messages across
workers gives two workers the same customer. Requirements are per customer, so
handling both messages fully is a duplicate, not extra credit.

Everything here is synthetic and harmless: invented names, invented orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable

from .seeding import make_rng

FIRST_NAMES = [
    "Ada", "Bruno", "Chloe", "Dmitri", "Esme", "Farah", "Gus", "Hana", "Ivo", "Jun",
    "Kemi", "Lars", "Mira", "Nico", "Oona", "Pavel", "Quinn", "Rosa", "Sami", "Tess",
    "Umar", "Vera", "Wes", "Ximena", "Yusuf", "Zoe",
]
LAST_NAMES = [
    "Abbott", "Barros", "Chen", "Dalton", "Eze", "Fischer", "Garza", "Holm", "Ito",
    "Jensen", "Kowal", "Lindqvist", "Moreau", "Nakamura", "Okafor", "Petrov", "Quist",
    "Rahman", "Silva", "Tanaka", "Ueda", "Varga", "Weber", "Yilmaz", "Zielinski",
]

ISSUES = ("damaged", "not_delivered", "wrong_item", "changed_mind", "late_delivery")

FIRST_TEMPLATES = {
    "damaged": "Hi, this is {name}. My order {order} arrived damaged. Can you help?",
    "not_delivered": "Hello, {name} here. Order {order} never arrived. What happened?",
    "wrong_item": "Hi, I'm {name}. I received the wrong item for order {order}.",
    "changed_mind": "Hi, {name} here. I changed my mind about order {order}. Can I get my money back?",
    "late_delivery": "Hello, this is {name}. Order {order} came a week late. Not happy.",
}
FOLLOW_UP = "Hi again, it's {name}. I wrote earlier about order {order} and have not heard back yet."

POSITIVE_KINDS = ("reply", "refund", "record:status")


@dataclass(frozen=True)
class Policy:
    eligible_issues: tuple = ("damaged", "not_delivered", "wrong_item")
    max_days: int = 30
    max_amount: int = 150

    def eligible(self, order: dict) -> bool:
        return (
            order["issue"] in self.eligible_issues
            and int(order["days_since_order"]) <= self.max_days
            and int(order["amount"]) <= self.max_amount
        )

    def text(self) -> str:
        issues = ", ".join(self.eligible_issues)
        return (
            f"Refund policy: refund the full order amount if and only if the issue is one of "
            f"[{issues}], the order is at most {self.max_days} days old, and the amount is at most "
            f"${self.max_amount}. Otherwise do not refund. Use lookup(customer) to see the order."
        )

    def to_dict(self) -> dict:
        return {
            "eligible_issues": list(self.eligible_issues),
            "max_days": self.max_days,
            "max_amount": self.max_amount,
            "text": self.text(),
        }


@dataclass(frozen=True)
class Requirement:
    id: str
    customer: str
    kind: str  # reply | refund | no_refund | record:status
    amount: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Batch:
    task_id: str
    seed: int
    messages: list  # list of dicts: id, customer, text
    customers: dict  # customer id -> order dict
    policy: Policy = field(default_factory=Policy)

    def eligible(self, customer: str) -> bool:
        return self.policy.eligible(self.customers[customer])

    def customers_for(self, message_ids: Iterable[str]) -> list:
        by_id = {m["id"]: m["customer"] for m in self.messages}
        out: list = []
        for mid in message_ids:
            c = by_id.get(mid)
            if c is not None and c not in out:
                out.append(c)
        return out

    def requirements(self) -> list:
        reqs = []
        for c in sorted(self.customers):
            reqs.append(Requirement(f"{c}:reply", c, "reply"))
            reqs.append(Requirement(f"{c}:record:status", c, "record:status"))
            if self.eligible(c):
                reqs.append(Requirement(f"{c}:refund", c, "refund", int(self.customers[c]["amount"])))
            else:
                reqs.append(Requirement(f"{c}:no_refund", c, "no_refund"))
        return reqs

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "seed": self.seed,
            "messages": [dict(m) for m in self.messages],
            "customers": {k: dict(v) for k, v in sorted(self.customers.items())},
            "policy": self.policy.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Batch":
        p = d["policy"]
        pol = Policy(tuple(p["eligible_issues"]), int(p["max_days"]), int(p["max_amount"]))
        return cls(d["task_id"], int(d["seed"]), [dict(m) for m in d["messages"]],
                   {k: dict(v) for k, v in d["customers"].items()}, pol)


def generate_batch(seed: int, n_messages: int = 12, n_repeat: int = 3, policy: Policy | None = None) -> Batch:
    """Generate a batch of ``n_messages`` messages from ``n_messages - n_repeat`` customers;
    ``n_repeat`` customers write twice (a first message and a follow-up later in the inbox)."""
    if not 0 <= n_repeat <= n_messages // 2:
        raise ValueError("n_repeat must be between 0 and n_messages // 2")
    policy = policy or Policy()
    rng = make_rng(seed, "tasks")
    n_customers = n_messages - n_repeat
    names = rng.sample([f"{f} {l}" for f in FIRST_NAMES for l in LAST_NAMES], n_customers)
    customers = {}
    for i, name in enumerate(names):
        cid = f"C{i + 1:02d}"
        customers[cid] = {
            "customer": cid,
            "name": name,
            "order_id": f"O{rng.randint(1000, 9999)}",
            "amount": rng.randint(20, 180),
            "days_since_order": rng.randint(1, 40),
            "issue": rng.choice(ISSUES),
        }
    cids = list(customers)
    first = cids[:]
    rng.shuffle(first)
    repeaters = sorted(rng.sample(cids, n_repeat))
    order = first[:]
    for c in repeaters:
        # place the follow-up strictly after the first message
        pos = order.index(c)
        order.insert(rng.randint(pos + 1, len(order)), c)
    messages = []
    seen: set = set()
    for i, c in enumerate(order):
        cust = customers[c]
        tmpl = FOLLOW_UP if c in seen else FIRST_TEMPLATES[cust["issue"]]
        seen.add(c)
        messages.append({"id": f"M{i + 1:02d}", "customer": c,
                         "text": tmpl.format(name=cust["name"], order=cust["order_id"])})
    return Batch(task_id=f"batch-{seed}", seed=seed, messages=messages, customers=customers, policy=policy)


# --------------------------------------------------------------------------- checking

def effective(effect: dict) -> bool:
    """An effect counts for the world if its final status is delivered (messages) or applied."""
    return effect.get("final") in ("delivered", "applied")


def requirement_met(req: dict, effects: Iterable[dict]) -> bool:
    """Check one requirement against ledger effects (already filtered by the caller, for
    example to those applied before a report). Uses each effect's final status."""
    c = req["customer"]
    kind = req["kind"]
    eff = [e for e in effects if e["target"] == c and effective(e)]
    if kind == "reply":
        return any(e["intent"] == "reply" for e in eff)
    if kind == "record:status":
        last = [e for e in eff if e["intent"] == "record:status"]
        return bool(last) and str(last[-1]["args"].get("value")) == "resolved"
    if kind == "refund":
        return any(e["intent"] == "refund" and _amount(e) == int(req["amount"]) for e in eff)
    if kind == "no_refund":
        return not any(e["intent"] == "refund" for e in eff)
    raise ValueError(f"unknown requirement kind {kind}")


def _amount(effect: dict):
    try:
        return int(round(float(effect["args"].get("amount"))))
    except (TypeError, ValueError):
        return None
