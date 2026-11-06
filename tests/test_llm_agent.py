import json

import pytest

from dst.agents.llm_agent import LLMAgent, parse_tool_call
from dst.agents.prompts import DISCIPLINE, ENV_NOTES, render_observation, system_prompt
from dst.agents.replay import ReplayMismatch
from dst.env import ROLE_TOOLS
from dst.run import replay_episode, run_episode
from fake_client import FakeClient


@pytest.mark.parametrize("text,tool", [
    ('{"tool": "noop", "args": {}}', "noop"),
    ('```json\n{"tool": "verify", "args": {"target": "C01"}}\n```', "verify"),
    ('Sure. {"tool": "post_board", "args": {"note": "a {brace} in a string"}} done', "post_board"),
    ('{"name": "lookup", "arguments": "{\\"customer\\": \\"C02\\"}"}', "lookup"),
    ('```\n{"tool":"report","args":{"status":"done","text":"x"}}\n```\ntrailing', "report"),
])
def test_parse_tool_call_robust(text, tool):
    call, ok = parse_tool_call(text)
    assert ok and call["tool"] == tool and isinstance(call["args"], dict)


@pytest.mark.parametrize("text", ["", "no json here", '{"tool": 3}', '{"args": {}}', '{"tool": "x", "args": [1]}', None])
def test_parse_tool_call_failures(text):
    assert parse_tool_call(text) == (None, False)


def test_prompts_per_role_and_condition():
    tools = ROLE_TOOLS["worker"]
    b0, p1 = system_prompt("worker", "B0", tools), system_prompt("worker", "P1", tools)
    assert DISCIPLINE not in b0 and DISCIPLINE in p1
    assert ENV_NOTES["idempotency"] in system_prompt("worker", "E1", tools)
    e3 = system_prompt("worker", "E3", tools)
    assert ENV_NOTES["idempotency"] in e3 and ENV_NOTES["readback_gate"] in e3 and DISCIPLINE not in e3
    lead = system_prompt("lead", "B0", ROLE_TOOLS["lead"])
    assert "send_message" not in lead and "delegate" in lead
    obs = {"agent": "w1", "role": "worker", "parent": "lead", "children": [], "t": 0, "turn": 0, "turns_left": 40,
           "condition": "P1", "task_id": "batch-1", "tools": tools, "new_messages": [], "last_call": None,
           "last_result": None}
    msg = render_observation(obs)
    assert "P1" not in msg and "condition" not in msg and msg.startswith("Turn 1 (40 turns left)")
    obs2 = dict(obs, new_messages=[{"kind": "delegation", "from": "lead", "task_id": "batch-1", "brief": "do it",
                                    "items": [{"id": "M01", "customer": "C01", "text": "hi"}],
                                    "policy": {"text": "Refund policy: ..."}, "t": 0}],
                last_call={"tool": "lookup", "args": {"customer": "C01"}}, last_result={"status": "ok"})
    msg2 = render_observation(obs2)
    assert "M01 from C01: hi" in msg2 and "[delegation from lead" in msg2 and 'lookup {"customer":"C01"}' in msg2
    ident = system_prompt("worker", "B0", tools, identity="You are w1 (worker).")
    assert "You are w1 (worker)." in ident


class _Boom:
    def complete(self, messages, *, model, temperature, seed):
        raise RuntimeError("provider down")


def test_llm_agent_counts_failures_and_noops():
    obs = {"agent": "w1", "role": "worker", "parent": "lead", "children": [], "t": 0, "turn": 0, "turns_left": 40,
           "condition": "B0", "task_id": "batch-1", "tools": ROLE_TOOLS["worker"], "new_messages": [],
           "last_call": None, "last_result": None}

    class Garbage:
        def complete(self, messages, *, model, temperature, seed):
            return "hmm"

    a = LLMAgent("w1", "worker", "B0", Garbage(), "m")
    assert a.act(obs) == {"tool": "noop", "args": {}} and a.parse_failures == 1
    ev = a.drain_events()
    assert ev[0][0] == "llm_call" and ev[0][1]["parse_ok"] is False and a.drain_events() == []
    b = LLMAgent("w1", "worker", "B0", _Boom(), "m")
    assert b.act(obs)["tool"] == "noop" and b.client_errors == 1
    assert "provider down" in b.drain_events()[0][1]["error"]


@pytest.mark.parametrize("shape,condition", [("F", "B0"), ("D", "E3")])
def test_llm_episode_with_fake_client_replays_byte_identical(tmp_path, shape, condition):
    out = tmp_path / "ep"
    logger, ledger, metrics = run_episode(shape=shape, condition=condition, agents="llm", client=FakeClient(),
                                          model="fake-1", seed=3, out=out)
    assert metrics["cost"]["llm_calls"] > 0 and metrics["cost"]["parse_failures"] > 0
    assert metrics["cost"]["input_tokens"] > 0 and len(ledger["effects"]) > 0
    events = logger.events()
    # every tool call is preceded by the llm_call that produced it
    for i, e in enumerate(events):
        if e["event"] == "tool_call":
            assert events[i - 1]["event"] == "llm_call" and events[i - 1]["agent"] == e["agent"]
    res = replay_episode(out, tmp_path / "replay")
    assert res["identical"], res


def test_replay_detects_a_tampered_response(tmp_path):
    out = tmp_path / "ep"
    run_episode(shape="F", condition="B0", agents="llm", client=FakeClient(garbage_every=0), model="fake-1", seed=4,
                out=out)
    lines = (out / "episode.jsonl").read_text().splitlines()
    for i, line in enumerate(lines):
        e = json.loads(line)
        if e["event"] == "llm_call" and e["agent"] == "w1" and e["payload"]["call_index"] == 1:
            e["payload"]["response"] = '{"tool": "noop", "args": {}}'
            lines[i] = json.dumps(e, sort_keys=True, separators=(",", ":"))
            break
    (out / "episode.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(ReplayMismatch):
        replay_episode(out, tmp_path / "replay")
