import math

from dst.env import FaultConfig
from dst.metrics import aggregate, episode_metrics, wilson
from helpers import call, make_office, team


def test_wilson_known_values():
    lo, hi = wilson(5, 10)
    assert math.isclose(lo, 0.2366, abs_tol=1e-3) and math.isclose(hi, 0.7634, abs_tol=1e-3)
    lo, hi = wilson(0, 20)
    assert lo == 0.0 and math.isclose(hi, 0.1611, abs_tol=1e-3)
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_attribution_retry_overlap_false_and_premature_done():
    office, batch, log = make_office(faults=FaultConfig(0.0, 0.0, 0.0, 2))
    mid = batch.messages[0]["id"]
    c = batch.messages[0]["customer"]
    agents = team(
        lead=[call("delegate", child="w1", items=[mid], brief="x"), call("delegate", child="w2", items=[mid], brief="x"),
              call("report", status="done", text="all done")],
        w1=[call("send_message", to=c, body="a"), call("send_message", to=c, body="a"),  # retry duplicate
            call("report", status="done", text="done")],                               # false: no record yet
        w2=[call("noop"), call("noop"), call("noop"), call("send_message", to=c, body="b"),  # overlap duplicate
            call("update_record", id=c, field="status", value="resolved")])
    led = office.run(agents)
    m = episode_metrics(led, log.schedule_records(), log.events())
    causes = sorted((d["agent"], d["cause"]) for d in m["attribution"]["duplicates"])
    assert causes == [("w1", "same_agent"), ("w2", "cross_agent")]
    w1_dup = [d for d in m["attribution"]["duplicates"] if d["agent"] == "w1"][0]
    assert w1_dup["step"] == 1 and w1_dup["intent"] == "reply"
    fd = {(f["agent"], f["step"]) for f in m["attribution"]["false_dones"]}
    assert ("w1", 2) in fd and ("lead", 2) in fd
    assert m["pcr"]["lead"] == [1, 1] and m["attribution"]["premature"][0]["agent"] == "lead"
    assert m["dar"]["extras"] == 2 and m["dar"]["required"] == len([r for r in led["requirements"] if r["kind"] != "no_refund"])
    agg = aggregate([m, m])
    assert agg["episodes"] == 2 and agg["fcr"]["all"]["n"] == 2 * m["fcr"]["all"][1]
    # fidelity: the lead claimed everything (no claims list) but almost nothing had landed
    assert m["fidelity"]["lead"]["claimed"] > m["fidelity"]["lead"]["claimed_true"]
