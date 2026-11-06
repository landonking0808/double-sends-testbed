"""The agent interface every policy (scripted or LLM-backed) implements.

The environment calls ``act(observation)`` once per scheduled turn. The agent returns
exactly one tool call::

    {"tool": "<tool name>", "args": {...}}

After each ``act`` the environment calls ``drain_events()`` and writes whatever the
agent returns there (for example ``llm_call`` payloads) into the episode log, before
the tool call itself. Agents never see or touch the ledger; they only see the
observation dict, which is a fresh copy every turn.

Observation keys (all JSON-serializable):

``agent, role, parent, children, t, turn, turns_left, condition, task_id, tools,
new_messages, last_call, last_result``

``new_messages`` holds inbox messages that arrived since the agent's previous turn
(``kind`` is one of ``task``, ``customer_message``, ``delegation``, ``report``,
``child_stopped``). ``last_call`` / ``last_result`` are the agent's own previous tool
call and the environment's answer to it (``None`` on the first turn).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

NOOP = {"tool": "noop", "args": {}}


class Agent(ABC):
    agent_id: str = ""
    role: str = ""

    @abstractmethod
    def act(self, observation: dict) -> dict:
        """Return one tool call ``{"tool": name, "args": {...}}``."""

    def drain_events(self) -> list:
        """Return (and clear) ``[(event_type, payload), ...]`` to log before the tool call."""
        return []

    def stats(self) -> dict:
        return {}


# Episode end reasons that mean "the run was cut short by the infrastructure, not by the
# team": such an episode is not a valid sample and a grid runner reruns it later.
ABORT_REASONS = ("aborted_quota", "aborted_client_error", "aborted_replay", "aborted_deadline")


class EpisodeStop(Exception):
    """Raised from ``act`` to end the whole episode now, before the agent's tool call.

    ``reason`` becomes ``ledger["end"]["reason"]``: one of ``ABORT_REASONS`` (an invalid
    sample: free quota reached, the provider kept failing) or ``call_cap`` (the episode
    used its LLM-call budget; a valid ending, like the per-agent turn cap)."""

    fatal = False

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class CallBudget:
    """A per-episode LLM call budget shared by every agent of the team."""

    def __init__(self, max_calls: int | None):
        self.max_calls = max_calls
        self.used = 0

    def take(self) -> bool:
        if self.max_calls is not None and self.used >= self.max_calls:
            return False
        self.used += 1
        return True
