from dst.tasks import Policy, generate_batch, requirement_met


def test_batch_is_seeded_and_has_repeats():
    a, b, c = generate_batch(3), generate_batch(3), generate_batch(4)
    assert a.to_dict() == b.to_dict() and a.to_dict() != c.to_dict()
    assert len(a.messages) == 12 and len(a.customers) == 9
    counts = {}
    for m in a.messages:
        counts[m["customer"]] = counts.get(m["customer"], 0) + 1
    assert sorted(counts.values()) == [1] * 6 + [2] * 3
    # a follow-up always comes after the customer's first message
    for cid, n in counts.items():
        if n == 2:
            idx = [i for i, m in enumerate(a.messages) if m["customer"] == cid]
            assert "Hi again" in a.messages[idx[1]]["text"] and "Hi again" not in a.messages[idx[0]]["text"]


def test_requirements_follow_policy():
    batch = generate_batch(5)
    reqs = batch.requirements()
    for cid, order in batch.customers.items():
        kinds = {r.kind for r in reqs if r.customer == cid}
        assert {"reply", "record:status"} <= kinds
        assert ("refund" in kinds) == Policy().eligible(order) and ("no_refund" in kinds) != ("refund" in kinds)


def test_requirement_checker():
    eff = lambda intent, final, **args: {"target": "C01", "intent": intent, "final": final, "args": args}
    assert requirement_met({"customer": "C01", "kind": "reply"}, [eff("reply", "delivered")])
    assert not requirement_met({"customer": "C01", "kind": "reply"}, [eff("reply", "bounced")])
    assert requirement_met({"customer": "C01", "kind": "refund", "amount": 40}, [eff("refund", "applied", amount=40)])
    assert not requirement_met({"customer": "C01", "kind": "refund", "amount": 40}, [eff("refund", "applied", amount=41)])
    assert not requirement_met({"customer": "C01", "kind": "no_refund"}, [eff("refund", "applied", amount=1)])
    assert requirement_met({"customer": "C01", "kind": "record:status"}, [eff("record:status", "applied", value="resolved")])
