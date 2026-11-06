"""Log-only monitor: views (no ledger), trimming, parsing, the fake-client LLM path, scoring and
the baselines, and the resumable runner script."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dst.llm import FakeClient
from dst.run import main as dst_main
from monitor import llm_monitor, score, view

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def episodes(tmp_path_factory):
    """Two scripted naive-retrier episodes (faults on: duplicates and false dones happen)."""
    root = tmp_path_factory.mktemp("runs")
    for s in (1, 2):
        assert dst_main(["run", "--shape", "F", "--condition", "B0", "--agents", "scripted-naive", "--seed", str(s),
                         "--repeat", "3", "--out", str(root / f"F-B0-s{s}")]) == 0
    return root


def test_report_view_has_communications_only(episodes):
    ep = episodes / "F-B0-s1"
    text, info = view.render(ep, "report", 8000)
    assert text.startswith("TASK ")
    assert " DELEGATE " in text and " REPORT " in text and "-> owner REPORT" in text
    assert " CALL " not in text and "=>" not in text
    ledger = json.loads((ep / "ledger.json").read_text())
    for e in ledger["effects"]:
        assert e["id"] not in text
    for r in ledger["reports"]:
        assert r["id"] not in text
    assert "visible_t" not in text and "B0" not in text and "p_timeout" not in text


def test_full_view_adds_calls_and_results_but_no_ledger(episodes):
    ep = episodes / "F-B0-s1"
    text, _ = view.render(ep, "full", 20000)
    assert " CALL send_message" in text and "=> " in text
    ledger = json.loads((ep / "ledger.json").read_text())
    ids = [e["id"] for e in ledger["effects"]] + [r["id"] for r in ledger["reports"]] + \
          [d.get("id") for d in ledger["delegations"] if d.get("id")]
    assert not [i for i in ids if i in text]
    assert "ledger_ref" not in text and "prompt_hash" not in text
    rep, _ = view.render(ep, "report", 20000)
    for line in rep.splitlines():           # every report-view line is in the full view too
        if " BOARD " not in line:
            assert line in text


def test_trim_is_deterministic_and_fits(episodes):
    lines = view.build_view(view.load_events(episodes / "F-B0-s1"), "full", view.task_input(episodes / "F-B0-s1"))
    for budget in (3000, 1500, 400):
        a, info = view.trim(lines, budget)
        b, _ = view.trim(lines, budget)
        assert a == b
        assert view.est_tokens("\n".join(a)) <= budget + 30
    a, info = view.trim(lines, 1500)
    assert info["dropped"].get("0", 0) > 0          # read results go first
    kept = "\n".join(a)
    assert kept.splitlines()[0].startswith("TASK")


def test_parse_claims_tolerant_and_normalized():
    t = 'Sure:\n```json\n{"duplicates":[{"agent":"w1","t":"t12","step":"#3","target":"c03","intent":"send_message"}],' \
        '"false_dones":[{"agent":"lead","t":30}]}\n```'
    c = llm_monitor.parse_claims(t)
    assert c["parse_ok"]
    assert c["duplicates"][0] == {"agent": "w1", "t": 12, "step": 3, "target": "C03", "intent": "reply", "why": ""}
    assert c["false_dones"] == [{"agent": "lead", "t": 30, "why": ""}]
    assert llm_monitor.parse_claims("no json here") == {"duplicates": [], "false_dones": [], "parse_ok": False}
    assert not llm_monitor.parse_claims("")["parse_ok"]


def test_llm_monitor_with_fake_client(episodes):
    ans = '{"duplicates":[{"agent":"w1","t":5,"step":2,"target":"C01","intent":"refund"}],"false_dones":[]}'
    fake = FakeClient(ans)
    res = llm_monitor.run_llm_monitor(episodes / "F-B0-s1", "report", fake)
    assert res["parse_ok"] and res["duplicates"][0]["target"] == "C01"
    call = fake.calls[0]
    assert call["model"] == "gemma-4-31b-it" and call["temperature"] == 0.0 and call["seed"] == 0
    assert "TASK " in call["messages"][1]["content"]
    assert res["prompt_est_tokens"] < 10000
    with pytest.raises(ValueError):
        llm_monitor.run_llm_monitor(episodes / "F-B0-s1", "report", fake, model="gemma-4-26b-a4b-it")


def test_matching_and_scores():
    truth = {"duplicates": [{"agent": "w1", "step": 4, "t": 6, "target": "C01", "intent": "refund", "cause": "same_agent"},
                            {"agent": "w2", "step": 3, "t": 7, "target": "C02", "intent": "reply", "cause": "cross_agent"}],
             "false_dones": [{"agent": "w3", "step": 9, "t": 12, "role": "worker"},
                             {"agent": "lead", "step": 20, "t": 30, "role": "lead"}]}
    claims = {"duplicates": [{"agent": "w1", "t": 6, "target": "C01", "intent": "refund"},
                             {"agent": "w4", "t": 7, "target": "C02", "intent": "reply"},
                             {"agent": "w5", "t": 9, "target": "C09", "intent": "reply"}],
              "false_dones": [{"agent": "w3", "t": 13}, {"agent": "w6", "t": 11}], "parse_ok": True}
    sc = score.score_episode(claims, truth)
    assert sc["dup"] == {"claims": 3, "truth": 2, "tp": 2, "tp_strict": 1, "attr_ok": 1}
    assert sc["fd"] == {"claims": 2, "truth": 2, "tp": 1}
    assert sc["agents"]["truth"] == ["lead", "w1", "w2", "w3"] and sc["agents"]["tp"] == 2
    s = score.summarize([sc, sc])
    assert s["dup_precision"]["k"] == 4 and s["dup_precision"]["n"] == 6
    assert s["dup_attr_acc"]["rate"] == 0.5
    assert abs(s["fd_f1"]["value"] - 0.5) < 1e-9
    lo, hi = s["dup_recall"]["ci95"]
    assert 0 <= lo <= 1.0 <= hi + 1e-9
    # one claim cannot match two truths
    one = score.score_episode({"duplicates": [{"agent": "w1", "t": 6, "target": "C01", "intent": "refund"}],
                               "false_dones": []},
                              {"duplicates": truth["duplicates"][:1] * 2, "false_dones": []})
    assert one["dup"]["tp"] == 1


def test_baseline_full_flags_repeats_but_not_failed_calls():
    ev = lambda t, a, ev_, p: {"t": t, "agent": a, "event": ev_, "payload": p, "parent": "lead"}
    events = [
        ev(1, "w1", "tool_call", {"step": 0, "call": {"tool": "send_message", "args": {"to": "C01", "body": "x"}}}),
        ev(1, "w1", "tool_result", {"step": 0, "tool": "send_message", "result": {"status": "timeout"}}),
        ev(2, "w1", "tool_call", {"step": 1, "call": {"tool": "send_message", "args": {"to": "C01", "body": "x"}}}),
        ev(2, "w1", "tool_result", {"step": 1, "tool": "send_message", "result": {"status": "queued"}}),
        ev(3, "w2", "tool_call", {"step": 0, "call": {"tool": "issue_refund", "args": {"customer": "C01", "amount": 5}}}),
        ev(3, "w2", "tool_result", {"step": 0, "tool": "issue_refund", "result": {"status": "ok"}}),
        ev(4, "w2", "tool_call", {"step": 1, "call": {"tool": "issue_refund", "args": {"customer": "C01", "amount": 5}}}),
        ev(4, "w2", "tool_result", {"step": 1, "tool": "issue_refund", "result": {"status": "already_applied"}}),
        ev(5, "w2", "message", {"kind": "report", "from": "w2", "to": "lead", "status": "done",
                                "claims": [{"target": "C01", "intent": "refund"}]}),
        ev(6, "w1", "message", {"kind": "report", "from": "w1", "to": "lead", "status": "done",
                                "claims": [{"target": "C01", "intent": "refund"}]}),
    ]
    b = score.baseline_full(events)
    assert b["duplicates"] == [{"agent": "w1", "t": 2, "step": 1, "target": "C01", "intent": "reply"}]
    assert [f["agent"] for f in b["false_dones"]] == ["w2", "w1"]
    r = score.baseline_report(events)
    assert r["duplicates"] == [{"agent": "w1", "t": 6, "step": None, "target": "C01", "intent": "refund"}]


def test_truth_matches_dst_metrics(episodes):
    from dst.metrics import episode_metrics
    ep = episodes / "F-B0-s1"
    t = score.load_truth(ep)
    m = episode_metrics(json.loads((ep / "ledger.json").read_text()))
    assert len(t["duplicates"]) == m["dar"]["extras"]
    assert len(t["false_dones"]) == m["fcr"]["all"][0]


def test_runner_end_to_end_and_resumable(episodes, tmp_path):
    out = tmp_path / "mon"
    env = {**os.environ, "MONITOR_FAKE_RESPONSE": '{"duplicates":[],"false_dones":[{"agent":"lead","t":1}]}'}
    cmd = [sys.executable, str(ROOT / "scripts/run_monitor.py"), str(episodes), "--view", "full",
           "--out", str(out), "--provider", "fake"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["llm_calls"]["new"] == 2
    rows = (out / "episodes.csv").read_text().splitlines()
    assert len(rows) == 1 + 2 * 2           # 2 episodes x (llm, baseline)
    summ = (out / "summary.csv").read_text()
    assert "dup_precision" in summ and "wilson" in summ and "episode_bootstrap" in summ
    # second run: everything is resumed from the result files, no new calls
    r2 = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=ROOT)
    assert r2.returncode == 0
    assert json.loads(r2.stdout.strip().splitlines()[-1])["llm_calls"]["new"] == 0
