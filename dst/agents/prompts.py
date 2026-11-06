"""Prompt text for LLM agents: system prompt per role and condition, the tool-call
format spec, and the rendering of an observation into the user message.

Conditions and prompts:

* B0 - role text + tool reference + format spec (no coordination advice).
* P1 - B0 plus ``DISCIPLINE`` (the in-agent control under test).
* E1/E2/E3 - B0 plus a short factual note on how the environment's API behaves
  (the ``task`` key is required; ``report(done)`` can be refused). This is API
  documentation, not advice: the control itself is enforced by the environment.
"""
from __future__ import annotations

import json

FORMAT_SPEC = (
    "Answer every turn with exactly one JSON object and nothing else:\n"
    '{"tool": "<tool name>", "args": {<arguments>}}\n'
    "One tool call per turn. Use only the tools listed for you. Use double quotes; no comments; no trailing text."
)

TOOL_SPECS = {
    "read_inbox": ("{}", "Return your inbox (customer messages, briefs from your parent, reports from agents you delegated to)."),
    "read_board": ("{}", "Return the shared team board (claims and notes posted by any agent)."),
    "lookup": ('{"customer": "C01"}', "Return the customer's order: amount, days since order, issue."),
    "verify": ('{"target": "C01"}', "Read back every effect the world has recorded on this customer (messages and their delivery status, refunds, record updates)."),
    "claim": ('{"target": "C01"}', "Post a claim on the board saying you are handling this customer."),
    "post_board": ('{"note": "text"}', "Post a note on the shared board."),
    "send_message": ('{"to": "C01", "body": "text", "task": "<task id>"}', "Send a message to the customer. SIDE EFFECT."),
    "issue_refund": ('{"customer": "C01", "amount": 42, "task": "<task id>"}', "Refund the customer. SIDE EFFECT."),
    "update_record": ('{"id": "C01", "field": "status", "value": "resolved", "task": "<task id>"}', "Set a field on the customer's record. SIDE EFFECT."),
    "delegate": ('{"child": "<agent id>", "items": ["M01", "M02"], "brief": "text"}', "Hand customer messages (by message id) to one of the agents below you."),
    "report": ('{"status": "done|failed|progress", "text": "text", "claims": [{"target": "C01", "intent": "reply|refund|record:status"}]}',
               "Report to your parent. done means all your assigned work is finished; list in claims the effects you believe happened."),
    "noop": ('{} or {"until": "message"}', "Do nothing this turn. With until=message you sleep until a new message arrives (use it to wait for reports)."),
}

ROLE_TEXT = {
    "lead": (
        "You are the lead of a small customer-support team in a simulated office. You receive a batch of customer "
        "messages. You cannot act on customers yourself: you have no tools that send, refund or change records. "
        "Split the messages among the agents directly below you with delegate, and report to the owner when the "
        "batch is handled."
    ),
    "manager": (
        "You are a manager in a simulated customer-support office. Your parent delegates customer messages to you. "
        "You cannot act on customers yourself. Split your messages among the workers directly below you with "
        "delegate, and report to your parent when your part is handled."
    ),
    "worker": (
        "You are a worker in a simulated customer-support office. Your parent delegates customer messages to you. "
        "For every customer in your messages: reply to the customer with send_message, refund the order with "
        "issue_refund if and only if the refund policy allows it, and set the customer's record status to resolved "
        "with update_record. Then report to your parent."
    ),
}

DISCIPLINE = (
    "Coordination discipline (follow it strictly):\n"
    "- Before acting on a customer, read the board and claim the customer. If another agent claimed that customer "
    "first, do not act on it.\n"
    "- Never repeat a side effect (message, refund, record update) that may already have happened. If a result is "
    "ambiguous, for example a timeout, call verify on the target before trying again.\n"
    "- Before reporting done, call verify on every customer you acted on and confirm each effect landed (a message "
    "that bounced did not reach the customer).\n"
    "- Do not report done while any agent you delegated to is still working."
)

ENV_NOTES = {
    "idempotency": (
        "Environment note: send_message, issue_refund and update_record require the argument task (the batch task id). "
        "The environment applies each (task, customer, kind of action) once; repeating it returns the earlier result. "
        "A customer that another agent is working on may be locked for a few turns."
    ),
    "readback_gate": (
        "Environment note: report with status done can be refused. It is refused unless you called verify on every "
        "customer you changed after your last change and the check passed, and while any agent you delegated to is "
        "still running. A refusal tells you the reason."
    ),
}

CONDITION_FLAGS = {
    "B0": (False, False, False),
    "P1": (True, False, False),
    "E1": (False, True, False),
    "E2": (False, False, True),
    "E3": (False, True, True),
}


def tool_reference(tools: list) -> str:
    lines = []
    for t in tools:
        sig, desc = TOOL_SPECS[t]
        lines.append(f"- {t} {sig}: {desc}")
    return "\n".join(lines)


def system_prompt(role: str, condition: str, tools: list) -> str:
    discipline, idem, gate = CONDITION_FLAGS[condition]
    parts = [ROLE_TEXT[role], "Your tools:\n" + tool_reference(tools)]
    if idem:
        parts.append(ENV_NOTES["idempotency"])
    if gate:
        parts.append(ENV_NOTES["readback_gate"])
    if discipline:
        parts.append(DISCIPLINE)
    parts.append(FORMAT_SPEC)
    return "\n\n".join(parts)


def render_observation(obs: dict) -> str:
    """Render one observation as the user message. The full observation is included as a
    JSON block (stable key order) after a short human-readable header."""
    head = [f"Turn {obs.get('turn', 0) + 1} (you have {obs.get('turns_left')} turns left). You are {obs.get('agent')} "
            f"({obs.get('role')}); parent: {obs.get('parent') or 'owner'}; agents below you: "
            f"{', '.join(obs.get('children') or []) or 'none'}. Task id: {obs.get('task_id')}."]
    n = len(obs.get("new_messages") or [])
    head.append(f"New messages: {n}." if n else "No new messages.")
    if obs.get("last_call") is not None:
        head.append("Your last tool call and its result are in last_call and last_result.")
    head.append("Reply with one JSON tool call.")
    shown = {k: v for k, v in obs.items() if k != "condition"}  # agents are not told the condition label
    return "\n".join(head) + "\n\nOBSERVATION\n" + json.dumps(shown, sort_keys=True, ensure_ascii=False)
