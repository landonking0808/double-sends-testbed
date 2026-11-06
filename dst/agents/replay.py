"""Exact replay of LLM responses from an episode log.

``ReplayClient`` satisfies the same ``complete(messages, *, model, temperature, seed)``
interface as a live client, but serves the responses recorded in the ``llm_call``
events of one agent, in order. It checks that the prompt it is asked about hashes to
the recorded ``prompt_hash``; any divergence raises ``ReplayMismatch`` (fatal), so a
replay either reproduces the episode exactly or fails loudly.
"""
from __future__ import annotations

from .llm_agent import prompt_hash


class ReplayMismatch(Exception):
    fatal = True


class ReplayedClientError(Exception):
    """Re-raises a client error that happened in the original run, with the same text."""

    def __init__(self, logged_error: str):
        super().__init__(logged_error)
        self.logged_error = logged_error


class ReplayClient:
    def __init__(self, llm_events: list, agent_id: str = ""):
        self.events = list(llm_events)
        self.agent_id = agent_id
        self.i = 0
        self.last_usage = None

    def complete(self, messages, *, model, temperature, seed) -> str:
        if self.i >= len(self.events):
            raise ReplayMismatch(f"{self.agent_id}: more LLM calls than recorded ({len(self.events)})")
        ev = self.events[self.i]
        self.i += 1
        h = prompt_hash(messages)
        for name, got, want in (("prompt_hash", h, ev["prompt_hash"]), ("model", model, ev["model"]),
                                ("seed", seed, ev["seed"]), ("temperature", temperature, ev["temperature"])):
            if got != want:
                raise ReplayMismatch(f"{self.agent_id} call {self.i - 1}: {name} differs ({got!r} != {want!r})")
        self.last_usage = ev.get("usage")
        if ev.get("error"):
            raise ReplayedClientError(ev["error"])
        return ev["response"]


def replay_clients(events: list) -> dict:
    """Map agent id -> ReplayClient from parsed episode events."""
    by_agent: dict = {}
    for e in events:
        if e["event"] == "llm_call":
            by_agent.setdefault(e["agent"], []).append(e["payload"])
    return {a: ReplayClient(evs, a) for a, evs in by_agent.items()}
