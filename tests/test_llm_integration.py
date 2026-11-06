"""Integration of the LLM agent with the runner: quota and persistent client failures stop
the episode (marked aborted) instead of filling it with no-ops; a per-episode call cap; real
token usage from the client's last_usage; the prompt budget; replay of stopped episodes."""
import json

from dst.agents.llm_agent import LLMAgent, est_tokens
from dst.agents.base import CallBudget
from dst.env import ROLE_TOOLS
from dst.llm import QuotaExhausted
from dst.llm.cache import CacheMiss
from dst.run import main, replay_episode, run_episode
from fake_client import IDENT, FakeClient


class QuotaAfter(FakeClient):
    """Plays the fake policy, then the free quota runs out after ``n`` calls."""

    def __init__(self, n, exc=None):
        super().__init__(garbage_every=0)
        self.limit, self.exc = n, exc

    def complete(self, messages, **kw):
        if self.n >= self.limit:
            self.n += 1
            raise (self.exc or QuotaExhausted("gemini:m: 12960 calls today; the next would pass 12960"))
        return super().complete(messages, **kw)


class FailAfter(FakeClient):
    def __init__(self, n):
        super().__init__(garbage_every=0)
        self.limit = n

    def complete(self, messages, **kw):
        if self.n >= self.limit:
            self.n += 1
            raise RuntimeError("HTTP 503 after 6 retries")
        return super().complete(messages, **kw)


def _llm_events(out):
    return [json.loads(x) for x in (out / "episode.jsonl").read_text().splitlines()
            if json.loads(x)["event"] == "llm_call"]


def test_quota_exhausted_stops_the_episode_as_aborted(tmp_path):
    out = tmp_path / "ep"
    client = QuotaAfter(5)
    _, ledger, m = run_episode(shape="F", condition="B0", agents="llm", client=client, model="fake", seed=2, out=out)
    assert ledger["end"]["reason"] == "aborted_quota" and m["end_reason"] == "aborted_quota" and m["aborted"]
    assert ledger["end"]["stop"]["detail"].startswith("QuotaExhausted")
    assert client.n == 6                                   # exactly one refused call, then nothing more
    evs = _llm_events(out)
    assert len(evs) == 6 and evs[-1]["payload"]["abort"] == "aborted_quota"
    man = json.loads((out / "manifest.json").read_text())
    assert man["aborted"] is True and man["end_reason"] == "aborted_quota"
    # no tool call follows the refused LLM call: the episode did not run on with no-ops
    lines = [json.loads(x) for x in (out / "episode.jsonl").read_text().splitlines()]
    assert lines[-1]["event"] == "llm_call"
    res = replay_episode(out, tmp_path / "rep")
    assert res["identical"], res


def test_replay_cache_miss_aborts_too(tmp_path):
    _, ledger, _ = run_episode(shape="F", condition="B0", agents="llm", client=QuotaAfter(3, CacheMiss("no hit")),
                               model="fake", seed=2)
    assert ledger["end"]["reason"] == "aborted_replay"


def test_persistent_client_failure_aborts_after_three_in_a_row(tmp_path):
    out = tmp_path / "ep"
    _, ledger, m = run_episode(shape="F", condition="E3", agents="llm", client=FailAfter(8), model="fake", seed=1,
                               out=out, max_consecutive_errors=3)
    assert ledger["end"]["reason"] == "aborted_client_error"
    stop = ledger["end"]["stop"]
    evs = [e for e in _llm_events(out) if e["agent"] == stop["agent"]]
    assert [e["payload"]["error"] is not None for e in evs[-3:]] == [True, True, True]
    assert m["cost"]["client_errors"] >= 3
    assert replay_episode(out, tmp_path / "rep")["identical"]


def test_isolated_client_errors_do_not_abort():
    class Flaky(FakeClient):
        def complete(self, messages, **kw):
            if self.n % 5 == 4:
                self.n += 1
                raise RuntimeError("one transient error")
            return super().complete(messages, **kw)

    _, ledger, m = run_episode(shape="F", condition="B0", agents="llm", client=Flaky(garbage_every=0), model="f", seed=1,
                          max_consecutive_errors=3)
    assert ledger["end"]["reason"] == "quiescent" and m["cost"]["client_errors"] > 0 and not m["aborted"]


def test_call_cap_ends_the_episode_and_replays(tmp_path):
    out = tmp_path / "ep"
    client = FakeClient(garbage_every=0)
    _, ledger, m = run_episode(shape="F", condition="B0", agents="llm", client=client, model="fake", seed=1,
                               out=out, max_llm_calls=10)
    assert ledger["end"]["reason"] == "call_cap" and not m["aborted"]
    assert client.n == 10 and m["cost"]["llm_calls"] == 10
    assert replay_episode(out, tmp_path / "rep")["identical"]


def test_real_usage_from_last_usage_is_counted(tmp_path):
    class Counted(FakeClient):
        def complete(self, messages, **kw):
            text = super().complete(messages, **kw)
            self.last_usage = {"input_tokens": 1000, "output_tokens": 7}
            return text

    _, _, m = run_episode(shape="F", condition="B0", agents="llm", client=Counted(garbage_every=0), model="f", seed=1)
    c = m["cost"]
    assert c["input_tokens"] == 1000 * c["llm_calls"] and c["output_tokens"] == 7 * c["llm_calls"]
    assert c["estimated_token_calls"] == 0


def test_usage_fields_missing_fall_back_to_the_estimate():
    class NoOut(FakeClient):
        def complete(self, messages, **kw):
            text = super().complete(messages, **kw)
            self.last_usage = {"input_tokens": 50, "output_tokens": None}
            return text

    _, _, m = run_episode(shape="F", condition="B0", agents="llm", client=NoOut(garbage_every=0), model="f", seed=1)
    c = m["cost"]
    assert c["input_tokens"] == 50 * c["llm_calls"] and c["output_tokens"] > 0
    assert c["estimated_token_calls"] == c["llm_calls"]


def test_prompt_budget_trims_old_history_but_keeps_the_brief():
    seen = []

    class Spy(FakeClient):
        def complete(self, messages, **kw):
            seen.append((messages, kw))
            return super().complete(messages, **kw)

    run_episode(shape="F", condition="P1", agents="llm", client=Spy(garbage_every=0), model="f", seed=1,
                max_prompt_tokens=900, history_turns=8, max_tokens=200)
    assert seen and all(kw.get("max_tokens") == 200 for _, kw in seen)
    first_user = {}
    for msgs, _ in seen:
        agent = IDENT.search(msgs[0]["content"]).group(1)
        first_user.setdefault(agent, msgs[1]["content"])
        assert msgs[1]["content"] == first_user[agent]       # the first exchange (the brief) is always there
        if len(msgs) > 4:   # history beyond the first exchange only while under budget
            assert sum(est_tokens(m["content"]) + 8 for m in msgs) <= 900


def test_parse_failure_feedback_is_neutral_and_next_turn_only():
    obs = {"agent": "w1", "role": "worker", "parent": "lead", "children": [], "t": 0, "turn": 0, "turns_left": 40,
           "condition": "B0", "task_id": "batch-1", "tools": ROLE_TOOLS["worker"], "new_messages": [],
           "last_call": None, "last_result": None}
    replies = iter(["thinking...", '{"tool": "noop", "args": {}}', '{"tool": "noop", "args": {}}'])
    got = []

    class C:
        def complete(self, messages, **kw):
            got.append(messages[-1]["content"])
            return next(replies)

    a = LLMAgent("w1", "worker", "B0", C(), "m", budget=CallBudget(None))
    for _ in range(3):
        a.act(obs)
    assert "not a valid JSON tool call" not in got[0] and "not a valid JSON tool call" in got[1]
    assert "not a valid JSON tool call" not in got[2]


def test_cli_exit_code_3_on_abort(tmp_path, monkeypatch, capsys):
    import dst.run as run
    monkeypatch.setattr(run, "get_llm_client", lambda provider: QuotaAfter(2))
    rc = main(["run", "--agents", "llm", "--provider", "fake", "--model", "m", "--seed", "1", "--out", str(tmp_path / "e")])
    assert rc == 3 and "ABORTED (aborted_quota)" in capsys.readouterr().out


def test_own_log_and_multi_call_feedback():
    from dst.agents.llm_agent import count_json_objects
    assert count_json_objects('{"tool": "a", "args": {}}\n{"tool": "b", "args": {"x": "}"}}') == 2
    assert count_json_objects('{"tool": "a", "args": {}}') == 1 and count_json_objects("no") == 0
    base = {"agent": "w1", "role": "worker", "parent": "lead", "children": [], "t": 0, "turns_left": 40,
            "condition": "B0", "task_id": "batch-1", "tools": ROLE_TOOLS["worker"]}
    got = []
    replies = iter(['{"tool": "lookup", "args": {"customer": "C01"}}\n{"tool": "noop", "args": {}}',
                    '{"tool": "send_message", "args": {"to": "C01", "body": "hello there", "task": "batch-1"}}',
                    '{"tool": "noop", "args": {}}'])

    class C:
        def complete(self, messages, **kw):
            got.append(messages)
            return next(replies)

    a = LLMAgent("w1", "worker", "B0", C(), "m", max_prompt_tokens=1500)
    a.act({**base, "turn": 0, "new_messages": [{"kind": "delegation", "from": "lead", "items": [{"id": "M01", "customer": "C01", "text": "hi"}], "policy": {"text": "p"}, "task_id": "batch-1"}], "last_call": None, "last_result": None})
    a.act({**base, "turn": 1, "new_messages": [], "last_call": {"tool": "lookup", "args": {"customer": "C01"}},
           "last_result": {"status": "ok", "order": {"amount": 33, "days_since_order": 4, "issue": "damaged"}}})
    a.act({**base, "turn": 2, "new_messages": [], "last_call": {"tool": "send_message", "args": {"to": "C01", "body": "hello there", "task": "batch-1"}},
           "last_result": {"status": "timeout", "error": "the request timed out"}})
    second, third = got[1][-1]["content"], got[2][-1]["content"]
    assert "only the first was executed" in second and "only the first" not in third
    assert "1: received delegation from lead: M01" in second and "1: lookup customer=C01 -> amount 33, 4 days, damaged" in second
    assert "2: send_message to=C01 body='hello there' task=batch-1 -> timeout" in third
    # the history holds the executed call, not the raw two-call reply, and no copy of the log
    assert got[2][2]["content"] == '{"tool": "lookup", "args": {"customer": "C01"}}'
    assert all("Your log so far" not in m["content"] for m in got[2][:-1])


def test_default_stops_on_the_first_persistent_failure():
    _, ledger, m = run_episode(shape="F", condition="B0", agents="llm", client=FailAfter(4), model="f", seed=1)
    assert ledger["end"]["reason"] == "aborted_client_error" and m["cost"]["client_errors"] == 1
