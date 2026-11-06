# Double Sends and False Dones

A seeded, rerunnable testbed for coordination failures in delegating LLM agent teams: duplicate side effects and false or premature "done" reports, measured against an environment-owned ground-truth ledger.

Built for the Apart Research Swarm Alignment Sprint (Nov 6-8 2026), Tracks 1 (Sandboxes and Testbeds) and 4 (Multi-Agent Oversight and Control).

**Prior work: none.** All code in this repository was written Nov 6-8 2026 during the sprint. There is no preexisting code.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest                      # unit tests + analytic validation of the metrics

# one scripted episode (no LLM, no network)
.venv/bin/python -m dst.run run --shape F --condition B0 --agents scripted-naive --seed 1 --out runs/demo
# reproduce it byte for byte
.venv/bin/python -m dst.run rerun --replay runs/demo
# pooled metrics with 95% intervals for any set of episode directories
.venv/bin/python -m dst.run metrics runs/demo
# an LLM team (provider clients live in dst/llm)
.venv/bin/python -m dst.run run --shape D --condition E3 --agents llm --provider <provider> --model <model> --seed 1 --out runs/llm-demo
```

Options: `--shape F|D|D+`, `--condition B0|P1|E1|E2|E3`, `--agents scripted-naive|scripted-careful|llm`,
`--seed`, fault rates `--p-timeout --p-bounce --p-stale --delivery-delay`, batch `--messages --repeat`,
`--turn-cap --max-ticks --lease-ticks --max-retries`, and for LLM teams `--temperature --history-turns
--max-prompt-tokens --max-tokens --max-llm-calls`. An LLM episode stops early, marked `aborted` in its manifest
(exit code 3), when a free quota is reached or a provider call keeps failing; it is not a valid sample and is
rerun from the response cache.

Grids (resumable, paced to the free limits, settings from `configs/frozen.json`; see [docs/PILOT.md](docs/PILOT.md)):

```bash
.venv/bin/python scripts/run_grid.py --model A --shapes F,D --conditions B0,P1,E1,E2,E3 --seeds 1-8 --out runs/grid/A
.venv/bin/python scripts/pilot_table.py runs/grid/A        # per-condition table of the finished episodes
```

## What is in the box

- **The office** (`dst/env.py`): a lead delegates a batch of customer messages down a tree
  (F: 1 lead + 6 workers; D: 1 lead, 2 managers, 3 workers each; D+: 1-3-5, 19 agents). Workers have
  side-effect tools (`send_message`, `issue_refund`, `update_record`), everyone has read and coordination
  tools (`read_inbox`, `read_board`, `lookup`, `verify`, `claim`, `post_board`, `delegate`, `report`,
  `noop`); the lead and managers have no side-effect tools and must delegate.
- **Ground truth**: an environment-owned effect ledger that agents cannot write. Agents only see copies
  of tool results; `verify(target)` is their read-back.
- **Seeded faults**: timeout-but-applied (`p_timeout`), accepted-then-bounced sends (`p_bounce`), stale
  board (claims visible to others one tick late, `p_stale_board`), and customers who write twice
  (`--repeat`), so naive splits overlap.
- **Seeded scheduler**: each tick, a seeded shuffle of the running agents, one tool call each, with a
  per-agent turn cap.
- **Conditions**: B0 none; P1 prompted discipline (inside the agents); E1 idempotency key
  `(task, target, intent)` applied once plus a lease lock per target; E2 read-back gate (`report(done)`
  refused unless `verify` passed after the agent's last side effect on every target, and refused while
  any descendant is still running); E3 = E1 + E2 (outside the agents).
- **Metrics** (`dst/metrics.py`), from the ledger and scheduler log only, never an LLM judge: duplicate
  action rate (DAR, per 100 required effects), false completion rate (FCR), premature completion rate
  (PCR), task success, report fidelity, cost, and attribution of every duplicate and false done to an
  agent and step; 95% Wilson intervals.
- **Scripted reference agents** (`dst/agents/scripted.py`): a naive retrier and a careful policy.
  `tests/test_metrics_analytic.py` checks the measured DAR/FCR of the naive retrier against closed-form
  expectations (`dst/analytic.py`) and that the careful policy reaches 0.
- **Logs and replay**: see [docs/LOG_SCHEMA.md](docs/LOG_SCHEMA.md). Scripted episodes replay from the
  seed; LLM episodes replay from the responses recorded in the log, with a prompt-hash check per call.

## Plugging in a model

`dst/agents/llm_agent.py` wraps any object with
`complete(messages, *, model, temperature, seed) -> str` (`messages` is a list of
`{"role": "system"|"user"|"assistant", "content": str}`). The agent sends one rendered observation per
turn and parses one JSON tool call `{"tool": name, "args": {...}}` from the answer (code fences and
surrounding prose are tolerated); a reply it cannot parse is counted and becomes `noop` (the next turn says
so). A client may set `last_usage = {"input_tokens", "output_tokens"}` after each call; those counts are used
for cost. Each prompt holds the system prompt, the first exchange (the brief), a one-line-per-step log of the
agent's own calls and received coordination messages, and as many recent full exchanges as fit the prompt
budget. Prompts per role and condition are in
`dst/agents/prompts.py`. Any other policy can implement `dst.agents.base.Agent.act(observation) -> dict`.
