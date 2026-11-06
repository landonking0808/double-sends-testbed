"""An Agent backed by any chat-completion client.

The client is any object with::

    complete(messages: list[dict], *, model: str, temperature: float, seed: int) -> str

where ``messages`` is ``[{"role": "system"|"user"|"assistant", "content": str}, ...]`` and
the return value is the raw assistant text. Optionally the client may set
``client.last_usage = {"input_tokens": int, "output_tokens": int}`` after each call; it is
logged when present; otherwise cost uses a deterministic chars/4 estimate. Provider clients, caching and backoff live outside this module.

Each turn the agent renders the observation into a user message, calls the client once,
and parses one JSON tool call from the answer (robust to code fences and surrounding
prose). On a parse failure, or an exception from the client, it counts the failure and
returns ``{"tool": "noop", "args": {}}``. Every call is logged as an ``llm_call`` event
holding the full response, so an episode can be replayed exactly (``ReplayClient``).
"""
from __future__ import annotations

import hashlib
import json

from ..seeding import derive_seed
from .base import NOOP, Agent
from .prompts import render_observation, system_prompt


def prompt_hash(messages: list) -> str:
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _strip_fences(text: str) -> str:
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        # parts at odd indexes are fenced blocks; prefer the first that holds a '{'
        for block in parts[1::2]:
            b = block
            if b[:4].lower() == "json":
                b = b[4:]
            if "{" in b:
                return b.strip()
    return t


def _first_json_object(text: str):
    """Return the first balanced {...} that parses as JSON, else None."""
    i = text.find("{")
    while i != -1:
        depth, in_str, esc = 0, False, False
        for j in range(i, len(text)):
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = text.find("{", i + 1)
    return None


def parse_tool_call(text):
    """Parse ``{"tool": ..., "args": {...}}`` from model output. Returns (call, ok)."""
    if not isinstance(text, str) or not text.strip():
        return None, False
    obj = _first_json_object(_strip_fences(text))
    if obj is None:
        obj = _first_json_object(text)
    if not isinstance(obj, dict):
        return None, False
    tool = obj.get("tool", obj.get("name"))
    args = obj.get("args", obj.get("arguments", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return None, False
    if args is None:
        args = {}
    if not isinstance(tool, str) or not tool or not isinstance(args, dict):
        return None, False
    return {"tool": tool, "args": args}, True


class LLMAgent(Agent):
    def __init__(self, agent_id: str, role: str, condition: str, client, model: str,
                 temperature: float = 0.7, seed: int = 0, history_turns: int = 8):
        self.agent_id = agent_id
        self.role = role
        self.condition = condition
        self.client = client
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.history_turns = history_turns
        self.calls = 0
        self.parse_failures = 0
        self.client_errors = 0
        self._system = None
        self._first: list = []    # first user/assistant exchange (holds the brief), always kept
        self._history: list = []  # later exchanges, windowed
        self._events: list = []

    def build_messages(self, observation: dict) -> list:
        if self._system is None:
            self._system = system_prompt(self.role, self.condition, observation["tools"])
        window = self._history[-2 * self.history_turns:] if self.history_turns > 0 else []
        return ([{"role": "system", "content": self._system}] + self._first + window
                + [{"role": "user", "content": render_observation(observation)}])

    def act(self, observation: dict) -> dict:
        messages = self.build_messages(observation)
        call_seed = derive_seed(self.seed, self.agent_id, "llm", self.calls) % (2 ** 31)
        phash = prompt_hash(messages)
        text, error = None, None
        if hasattr(self.client, "last_usage"):
            try:
                self.client.last_usage = None  # never log a stale usage from the previous call
            except AttributeError:
                pass
        try:
            text = self.client.complete(messages, model=self.model, temperature=self.temperature, seed=call_seed)
        except Exception as e:  # noqa: BLE001 - any client failure becomes a counted no-op
            if getattr(e, "fatal", False):
                raise
            error = getattr(e, "logged_error", None) or f"{type(e).__name__}: {e}"
            self.client_errors += 1
        usage = getattr(self.client, "last_usage", None)
        usage = dict(usage) if isinstance(usage, dict) else None
        call, ok = parse_tool_call(text) if text is not None else (None, False)
        if not ok:
            if error is None:
                self.parse_failures += 1
            call = dict(NOOP)
        exchange = [messages[-1], {"role": "assistant", "content": text if text is not None else ""}]
        if not self._first:
            self._first = exchange
        else:
            self._history.extend(exchange)
        self._events.append(("llm_call", {
            "call_index": self.calls, "model": self.model, "temperature": self.temperature, "seed": call_seed,
            "prompt_hash": phash, "n_messages": len(messages), "response": text, "usage": usage,
            # deterministic size estimate (chars / 4), used for cost when the client reports no usage
            "est_tokens": {"input_tokens": -(-sum(len(m["content"]) for m in messages) // 4),
                           "output_tokens": -(-len(text or "") // 4)},
            "parse_ok": ok, "error": error,
        }))
        self.calls += 1
        return call

    def drain_events(self) -> list:
        ev, self._events = self._events, []
        return ev

    def stats(self) -> dict:
        return {"calls": self.calls, "parse_failures": self.parse_failures, "client_errors": self.client_errors}
