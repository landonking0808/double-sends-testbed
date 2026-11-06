import pytest

from dst.env import CONDITIONS, ROLE_TOOLS, SIDE_EFFECT_TOOLS, FaultConfig, build_tree
from dst.tasks import requirement_met
from helpers import call, make_office, team


def m1(batch):
    return batch.messages[0]["id"], batch.messages[0]["customer"]


def results(log, agent):
    return [e["payload"]["result"] for e in log.events() if e["event"] == "tool_result" and e["agent"] == agent]


def test_shapes_and_roles():
    assert len(build_tree("F")) == 7 and len(build_tree("D")) == 9 and len(build_tree("D+")) == 19
    for s in build_tree("D"):
        if s.role in ("lead", "manager"):
            assert not set(SIDE_EFFECT_TOOLS) & set(ROLE_TOOLS[s.role])
    assert set(CONDITIONS) == {"B0", "P1", "E1", "E2", "E3"}


def test_lead_has_no_side_effect_tools():
    office, batch, log = make_office()
    mid, c = m1(batch)
    agents = team(lead=[call("send_message", to=c, body="hi"), call("issue_refund", customer=c, amount=5)])
    led = office.run(agents)
    assert led["effects"] == []
    assert all("not available to a lead" in r["error"] for r in results(log, "lead")[:2])


def test_agents_cannot_write_the_ledger():
    office, batch, log = make_office()
    mid, c = m1(batch)

    def tamper(obs):
        # mutate everything the agent can see; the environment must not change
        res = obs["last_result"]
        res["effects"].append({"agent": "w1", "intent": "refund", "status": "applied", "amount": 999})
        res["pass"] = True
        return call("verify", target=c)

    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi"), call("verify", target=c), tamper,
                      call("verify", target=c)])
    led = office.run(agents)
    assert len(led["effects"]) == 1
    final_verify = [r for r in results(log, "w1") if "effects" in r][-1]
    assert [e["intent"] for e in final_verify["effects"]] == ["reply"]
    assert not hasattr(office, "write") and all(not t.startswith("write") for t in ROLE_TOOLS["worker"])


def test_timeout_but_applied():
    office, batch, log = make_office(faults=FaultConfig(p_timeout=1.0, p_bounce=0.0, p_stale_board=0.0))
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi"), call("send_message", to=c, body="hi")])
    led = office.run(agents)
    assert [r["status"] for r in results(log, "w1")[:2]] == ["timeout", "timeout"]
    assert [e["final"] for e in led["effects"]] == ["delivered", "delivered"]  # both landed: a duplicate


def test_accepted_then_bounced_and_verify_sees_it():
    office, batch, log = make_office(faults=FaultConfig(p_timeout=0.0, p_bounce=1.0, p_stale_board=0.0, delivery_delay=2))
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi"), call("verify", target=c), call("noop"),
                      call("verify", target=c)])
    led = office.run(agents)
    r = results(log, "w1")
    assert r[0]["status"] == "queued"
    assert r[1]["effects"][0]["status"] == "queued" and r[1]["pending"] and not r[1]["pass"]
    assert r[3]["effects"][0]["status"] == "bounced" and not r[3]["pass"]
    req = {"customer": c, "kind": "reply"}
    assert led["effects"][0]["final"] == "bounced" and not requirement_met(req, led["effects"])


def test_E1_idempotency_key_and_bounce_release():
    office, batch, log = make_office(condition="E1")
    mid, c = m1(batch)
    task = batch.task_id
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi"),  # no key -> refused
                      call("send_message", to=c, body="hi", task=task),
                      call("send_message", to=c, body="hi again", task=task),
                      call("update_record", id=c, field="status", value="resolved", task=task)])
    led = office.run(agents)
    r = results(log, "w1")
    assert r[0]["status"] == "error" and "idempotency key" in r[0]["error"]
    assert r[1]["status"] == "queued"
    assert r[2]["status"] == "already_applied"
    assert len(led["effects"]) == 2 and len(led["suppressed"]) == 1

    office, batch, log = make_office(condition="E1", faults=FaultConfig(0.0, 1.0, 0.0, 2))
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi", task=task), call("noop"), call("noop"),
                      call("send_message", to=c, body="hi", task=task)])
    led = office.run(agents)
    assert len(led["effects"]) == 2  # a bounced effect does not block a retry of its key


def test_E1_lease_lock_blocks_other_agent():
    office, batch, log = make_office(condition="E1")
    mid, c = m1(batch)
    task = batch.task_id
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x"),
                        call("delegate", child="w2", items=[mid], brief="x")],
                  w1=[call("send_message", to=c, body="hi", task=task)],
                  w2=[call("update_record", id=c, field="status", value="resolved", task=task)])
    led = office.run(agents)
    assert results(log, "w2")[0]["status"] == "locked"
    assert len(led["effects"]) == 1 and led["blocked"][0]["holder"] == "w1"


def test_E2_readback_gate():
    office, batch, log = make_office(condition="E2", faults=FaultConfig(0.0, 0.0, 0.0, delivery_delay=3))
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x"), call("report", status="done", text="x")],
                  w1=[call("send_message", to=c, body="hi"),
                      call("report", status="done", text="x"),   # no verify yet
                      call("verify", target=c),                    # still queued -> fails
                      call("report", status="done", text="x"),
                      call("noop"), call("verify", target=c),      # delivered -> passes
                      call("report", status="done", text="x")])
    led = office.run(agents)
    reps = [(r["agent"], r["accepted"], r["reason"]) for r in led["reports"]]
    lead_rep = [r for r in reps if r[0] == "lead"]
    assert lead_rep[0][1] is False and "still running" in lead_rep[0][2]
    w = [r for r in reps if r[0] == "w1"]
    assert [x[1] for x in w] == [False, False, True]
    assert "call verify" in w[0][2] and "did not pass" in w[1][2]


def test_B0_has_no_gate():
    office, batch, log = make_office(condition="B0")
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x"), call("report", status="done", text="x")])
    led = office.run(agents)
    assert all(r["accepted"] for r in led["reports"])


def test_stale_board_one_tick():
    office, batch, log = make_office(faults=FaultConfig(0.0, 0.0, 1.0, 2))
    office.t = 5
    office._post("w1", "claim", target="C01")
    assert office._visible_board("w2") == [] and len(office._visible_board("w1")) == 1
    office.t = 6
    assert len(office._visible_board("w2")) == 1


def test_turn_cap_and_child_stopped():
    office, batch, log = make_office(turn_cap=3)
    mid, c = m1(batch)
    agents = team(lead=[call("delegate", child="w1", items=[mid], brief="x")],
                  w1=[call("noop"), call("noop"), call("noop"), call("noop")])
    led = office.run(agents)
    assert led["turns"]["w1"] == 3 and led["final_status"]["w1"] == "exhausted"
    stopped = [m for m in agents["lead"].observations[-1]["new_messages"] if m["kind"] == "child_stopped"]
    assert stopped and stopped[0]["child"] == "w1"


def test_delegate_validation():
    office, batch, log = make_office(shape="D")
    mid, c = m1(batch)
    agents = team("D", lead=[call("delegate", child="w1", items=[mid]), call("delegate", child="m1", items=["M99"]),
                             call("delegate", child="m1", items=[mid])],
                  m1=[call("delegate", child="w4", items=[mid]), call("delegate", child="w1", items=[mid])])
    led = office.run(agents)
    r = results(log, "lead")
    assert r[0]["status"] == "error" and r[1]["status"] == "error" and r[2]["status"] == "ok"
    rm = results(log, "m1")
    assert rm[0]["status"] == "error" and rm[1]["status"] == "ok"
    assert led["items"]["w1"] == [mid]


@pytest.mark.parametrize("condition", ["B0", "E3"])
def test_scheduler_is_seeded(condition):
    from dst.run import run_episode
    a = run_episode(shape="F", condition=condition, agents="scripted-naive", seed=7)[0].texts()
    b = run_episode(shape="F", condition=condition, agents="scripted-naive", seed=7)[0].texts()
    c = run_episode(shape="F", condition=condition, agents="scripted-naive", seed=8)[0].texts()
    assert a == b
    assert a["scheduler.jsonl"] != c["scheduler.jsonl"]
