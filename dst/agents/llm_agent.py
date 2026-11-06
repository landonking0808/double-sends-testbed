"""An Agent backed by any chat-completion client.

The client is any object with::

    complete(messages: list[dict], *, model: str, temperature: float, seed: int) -> str

where ``messages`` is ``[{"role": "system"|"user"|"assistant", "content": str}, ...]`` and
the return value is the raw assistant text. Optionally the client may set
``client.last_usage = {"input_tokens": int, "output_tokens": int}`` after each call; it is
logged when present; otherwise cost uses a deterministic chars/4 estimate. Provider clients, caching and backoff live outside this module.

Each turn the agent renders the observation into a user message, calls the client once,
and parses one JSON tool call from the answer (robust to code fences and surrounding
prose). On a parse failure, or a transient exception from the client, it counts the
failure and returns ``{"tool": "noop", "args": {}}``. Every call is logged as an
``llm_call`` event holding the full response, so an episode can be replayed exactly
(``ReplayClient``).

The episode is stopped (``EpisodeStop``) instead of filling up with no-ops when

* the client raises an exception with an ``abort_reason`` attribute (``QuotaExhausted``:
  the free daily quota is reached and nothing was sent; ``CacheMiss`` in replay-only mode),
* ``max_consecutive_errors`` calls in a row of one agent failed (after the client's own
  retries and backoff): ``aborted_client_error``,
* the team's shared ``CallBudget`` is used up: ``call_cap`` (a valid ending).

Prompt size and memory: the system prompt and the first exchange (it holds the brief) are
always sent. Each user message starts with the agent's own log, one short line per earlier
tool call and its result and per coordination message received (delegations, reports), so
the agent keeps track of its work without the full history. Later full exchanges (the
assistant side is the call that was executed, in canonical JSON) are kept newest first
while the estimated prompt stays under ``max_prompt_tokens`` (at most ``history_turns``).
The log and the windowing are identical in every condition.
"""
from __future__ import annotations

import hashlib
import json

from ..seeding import derive_seed
from .base import NOOP, Agent, CallBudget, EpisodeStop
from .prompts import (identity_line, render_log, render_observation, summarize_call, summarize_message,
                      system_prompt)

PARSE_FEEDBACK = ("Your previous reply was not a valid JSON tool call, so it was treated as noop. "
                  'Answer with exactly one JSON object {"tool": ..., "args": {...}}.')
MULTI_FEEDBACK = "Your previous reply held {n} tool calls; only the first was executed."
LOG_LINES = 40


def count_json_objects(text: str) -> int:
    """How many top-level JSON objects with a "tool" key the reply holds (format feedback only)."""
    dec, i, n = json.JSONDecoder(), 0, 0
    text = text or ""
    while True:
        i = text.find("{", i)
        if i == -1:
            return n
        try:
            obj, end = dec.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and ("tool" in obj or "name" in obj):
            n += 1
        i = end


def est_tokens(text: str) -> int:
    """Same conservative estimate the client uses before a call (about 3.5 chars a token)."""
    return int(-(-len(text) // 3.5)) if text else 0


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
                 temperature: float = 0.7, seed: int = 0, history_turns: int = 8,
                 max_prompt_tokens: int | None = None, max_tokens: int | None = None,
                 budget: CallBudget | None = None, max_consecutive_errors: int = 3):
        self.agent_id = agent_id
        self.role = role
        self.condition = condition
        self.client = client
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.history_turns = history_turns
        self.max_prompt_tokens = max_prompt_tokens
        self.max_tokens = max_tokens
        self.budget = budget
        self.max_consecutive_errors = max_consecutive_errors
        self.calls = 0
        self.parse_failures = 0
        self.client_errors = 0
        self._consecutive_errors = 0
        self._feedback = None     # format feedback for the next turn (parse failure, several calls)
        self._log: list = []      # one line per own call + result and per coordination message
        self._system = None
        self._first: list = []    # first user/assistant exchange (holds the brief), always kept
        self._history: list = []  # later exchanges, windowed
        self._events: list = []

    def _observe(self, observation: dict) -> None:
        """Update the agent's own log from the observation (its last call's result, new messages)."""
        turn = observation.get("turn", 0)
        if observation.get("last_call") is not None:
            self._log.append(f"{turn}: " + summarize_call(observation["last_call"], observation.get("last_result")))
        for m in observation.get("new_messages") or []:
            line = summarize_message(m)
            if line:
                self._log.append(f"{turn + 1}: {line}")
        del self._log[:-LOG_LINES]

    def build_messages(self, observation: dict) -> list:
        """The prompt for this turn. ``act`` updates the log first; this function has no side effects."""
        if self._system is None:
            self._system = system_prompt(self.role, self.condition, observation["tools"],
                                         identity=identity_line(observation))
        user = render_observation(observation)
        prefix = []
        if self._feedback:
            prefix.append(self._feedback)
        if self._log and self._first:
            prefix.append(render_log(self._log))
        current = "\n".join(prefix + [user])
        head = [{"role": "system", "content": self._system}] + self._first
        tail = [{"role": "user", "content": current}]
        pairs = [self._history[i:i + 2] for i in range(0, len(self._history), 2)]
        pairs = pairs[-self.history_turns:] if self.history_turns > 0 else []
        if self.max_prompt_tokens is not None:
            used = sum(est_tokens(m["content"]) + 8 for m in head + tail)
            kept = []
            for pair in reversed(pairs):
                cost = sum(est_tokens(m["content"]) + 8 for m in pair)
                if used + cost > self.max_prompt_tokens:
                    break
                kept.insert(0, pair)
                used += cost
            pairs = kept
        self._current_plain = user
        return head + [m for p in pairs for m in p] + tail

    def _call(self, messages, call_seed):
        kw = {"model": self.model, "temperature": self.temperature, "seed": call_seed}
        if self.max_tokens is not None:
            kw["max_tokens"] = self.max_tokens
        return self.client.complete(messages, **kw)

    def act(self, observation: dict) -> dict:
        if self.budget is not None and not self.budget.take():
            raise EpisodeStop("call_cap", f"the team used its {self.budget.max_calls} LLM calls")
        self._observe(observation)
        messages = self.build_messages(observation)
        call_seed = derive_seed(self.seed, self.agent_id, "llm", self.calls) % (2 ** 31)
        phash = prompt_hash(messages)
        text, error, abort = None, None, None
        if hasattr(self.client, "last_usage"):
            try:
                self.client.last_usage = None  # never log a stale usage from the previous call
            except AttributeError:
                pass
        try:
            text = self._call(messages, call_seed)
        except Exception as e:  # noqa: BLE001 - a client failure becomes a counted no-op, or stops the episode
            if getattr(e, "fatal", False):
                raise
            error = getattr(e, "logged_error", None) or f"{type(e).__name__}: {e}"
            abort = getattr(e, "abort_reason", None)
            self.client_errors += 1
            self._consecutive_errors += 1
            if abort is None and self._consecutive_errors >= self.max_consecutive_errors:
                abort = "aborted_client_error"
        else:
            self._consecutive_errors = 0
        usage = getattr(self.client, "last_usage", None)
        usage = dict(usage) if isinstance(usage, dict) else None
        call, ok = parse_tool_call(text) if text is not None else (None, False)
        if not ok:
            if error is None:
                self.parse_failures += 1
            call = dict(NOOP)
        self._feedback = None
        if error is None and not ok:
            self._feedback = PARSE_FEEDBACK
        elif ok:
            n = count_json_objects(text)
            if n > 1:
                self._feedback = MULTI_FEEDBACK.format(n=n)
        # history keeps the observation without the log, and the call that was executed
        said = json.dumps(call, ensure_ascii=False) if ok else (text or "")[:300]
        exchange = [{"role": "user", "content": self._current_plain}, {"role": "assistant", "content": said}]
        if not self._first:
            self._first = exchange
        else:
            self._history.extend(exchange)
        self._events.append(("llm_call", {
            "call_index": self.calls, "model": self.model, "temperature": self.temperature, "seed": call_seed,
            "max_tokens": self.max_tokens,
            "prompt_hash": phash, "n_messages": len(messages), "response": text, "usage": usage,
            # deterministic size estimate (chars / 4), used for cost when the client reports no usage
            "est_tokens": {"input_tokens": -(-sum(len(m["content"]) for m in messages) // 4),
                           "output_tokens": -(-len(text or "") // 4)},
            "parse_ok": ok, "error": error, "abort": abort,
        }))
        self.calls += 1
        if abort is not None:
            raise EpisodeStop(abort, error or "")
        return call

    def drain_events(self) -> list:
        ev, self._events = self._events, []
        return ev

    def stats(self) -> dict:
        return {"calls": self.calls, "parse_failures": self.parse_failures, "client_errors": self.client_errors}
