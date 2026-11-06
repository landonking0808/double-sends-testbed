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
