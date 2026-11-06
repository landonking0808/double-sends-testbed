"""A deterministic fake chat client for tests: plays a simple policy from the rendered
observation (identity from the system prompt, message ids and customers from the
"M01 from C01:" lines of the last user message), in varying output formats, with
occasional garbage."""
import json
import re

IDENT = re.compile(r"You are (\S+) \((\w+)\)\. Your parent: \S+\. Agents below you: ([^.]*)\. Batch task id: (\S+)\.")
ITEM = re.compile(r"^\s*-?\s*(M\d+) from (C\d+):", re.M)


class FakeClient:
    def __init__(self, garbage_every=7):
        self.state = {}
        self.n = 0
        self.garbage_every = garbage_every
        self.last_usage = None

    def complete(self, messages, *, model, temperature, seed, max_tokens=None):
        self.n += 1
        text = messages[-1]["content"]
        agent, role, kids, task_id = IDENT.search(messages[0]["content"]).groups()
        kids = [] if kids == "none" else kids.split(", ")
        obs = {"agent": agent, "role": role, "children": kids, "task_id": task_id,
               "items": ITEM.findall(text.split("Your last call:")[0])}
        call = self._policy(obs)
        self.last_usage = {"input_tokens": sum(len(m["content"]) for m in messages) // 4, "output_tokens": 20}
        if self.garbage_every and self.n % self.garbage_every == 0:
            return "Let me think about which tool to use next."   # no JSON: a parse failure
        body = json.dumps(call)
        fmt = seed % 3
        if fmt == 0:
            return body
        if fmt == 1:
            return "```json\n" + body + "\n```"
        return "Next step: " + body + " (that should do it)"

    def _policy(self, obs):
        me = obs["agent"]
        st = self.state.setdefault(me, {"items": [], "plan": None})
        st["items"] += obs["items"]
        if st["plan"] is None and st["items"]:
            if obs["role"] in ("lead", "manager"):
                kids = obs["children"]
                chunks = {k: [] for k in kids}
                for i, (mid, _) in enumerate(st["items"]):
                    chunks[kids[i % len(kids)]].append(mid)
                st["plan"] = [{"tool": "delegate", "args": {"child": k, "items": v, "brief": "handle these"}}
                              for k, v in chunks.items() if v]
            else:
                st["plan"] = []
                for _, c in st["items"]:
                    st["plan"].append({"tool": "send_message", "args": {"to": c, "body": "Hello", "task": obs["task_id"]}})
                    st["plan"].append({"tool": "update_record",
                                       "args": {"id": c, "field": "status", "value": "resolved", "task": obs["task_id"]}})
            st["plan"].append({"tool": "report", "args": {"status": "done", "text": "finished"}})
        if st["plan"]:
            return st["plan"].pop(0)
        return {"tool": "noop", "args": {"until": "message"}}
