# Log-only monitors (Track 4)

Question: can an overseer that reads only what agents say (no ledger) find the same failures, and the
same responsible agents, as the environment's ground-truth ledger? Code in `monitor/`, runner
`scripts/run_monitor.py`, tests `tests/test_monitor.py`.

## The two views

Both are built from `episode.jsonl` by `monitor/view.py` and rendered as one short text line per event,
in log order, with the tick (`t12`) and the agent's own step number (`w3#5`, full view only).

| | `report` view | `full` view |
|---|---|---|
| Task header: task id and message id -> customer (`M01=C01 ...`) | yes | yes |
| Delegations (from, to, item ids, brief, first 80 chars) | yes | yes |
| Reports delivered to a parent (status, claimed effects `C03:reply`, text, first 140 chars) | yes | yes |
| The top agent's accepted report to the owner | yes | yes |
| Board posts (`claim` target, `post_board` note), at the tick they were posted | yes | yes |
| Turn-cap notices delivered to a parent (`w3 stopped (turn cap reached)`) | yes | yes |
| Every tool call an agent made (args as given; message bodies cut to 50 chars; the batch `task` arg dropped) | no | yes |
| Every tool result exactly as that agent saw it (`ok`, `queued`, `timeout`, `already_applied`, `locked`, `refused` + reason, `error`) | no | yes |
| `verify` results (the effects the environment showed that agent for that customer at that tick, incl. delivery status) | no | yes (agents saw them) |
| Report attempts the environment refused (E2) | no (never delivered) | as the agent's `report` call + `refused` result |

Never in either view: `ledger.json` (except the task input in the header), `scheduler.jsonl`,
`metrics.json`, `manifest.json` (condition label, fault rates, model), `ledger_ref` and any ledger id
(effect `e0007`, report `r003`, delegation `d002` are stripped even from tool results), `llm_call`
events (prompts, raw responses, tokens), board visibility ticks (the stale-board fault), delivery
outcomes (a bounce) other than what an agent's own `verify` showed, and any ground-truth label.

The task header is read from `ledger.json["batch"]` (only `task_id` and each message's id and
customer: no message text, order data, policy or requirements), because the lead's first observation,
which carries it, is not logged as text. An overseer knows the task it is overseeing.

Long views are trimmed deterministically to a token budget (default 8,000, estimated at 3.5 chars a
token like the client's pre-call estimate): first `read_inbox`/`read_board`/`lookup`/`noop` results,
then those calls, then `verify` calls and results (oldest first in each class); then lines are
shortened to 160 and 100 chars; as a last resort the middle is cut with a marker. Communications and
side-effect calls/results are never dropped, only shortened. Each result file records what was trimmed.
On the pilot (shape F) no report view and one full view (6 read results) needed trimming.

## The monitors

**LLM monitor** (`monitor/llm_monitor.py`): `gemma-4-31b-it` on the Gemini API free tier (its own
per-model quota: 30 RPM / 16K input TPM / 14.4K RPD; Gemma 4 is free of charge, $0), temperature 0,
seed 0, max 1,024 output tokens, thinking `minimal` (the client default). It goes through the shared
`dst.llm` client, so every answer is cached in `runs/cache/`, pacing is held at 80% of RPM and TPM and
the daily cap at 90% of RPD (`dst/llm/limits.py` has the entry), and 429/5xx back off. The grid's
models (`gemma-4-26b-a4b-it`, the Cloudflare model) are refused. Prompt: a description of the office
and its known hazards (ambiguous timeouts, bounces, double-writing customers, late claims), the view,
and a request for one JSON object:

```json
{"duplicates": [{"agent": "w1", "t": 12, "step": 5, "target": "C03", "intent": "reply|refund|record:status", "why": "..."}],
 "false_dones": [{"agent": "w2", "t": 20, "why": "..."}]}
```

A reply that does not parse counts as no claims and is counted in `parse_failures`.

**Rule baselines** (`monitor/score.py`), one per view, no LLM:

* full view: every side-effect call on a (customer, intent) that some agent already called before is
  a duplicate, blamed on the caller, unless its own result said it did not apply (`error`, `locked`,
  `refused`, `already_applied`, `held`); every delivered done report is a false done.
* report view: a (customer, intent) claimed in done reports by two different leaf agents (agents that
  never delegated) is a duplicate, blamed on the later reporter; every delivered done report is a false done.

"Flag every done" is the recall ceiling for false dones; its precision equals the share of dones that
are false.

## Scoring against the ledger

Truth is `dst.metrics.episode_metrics(ledger)["attribution"]`: every extra effective side effect
(agent, step, tick, target, intent) and every accepted done report that did not meet its node's
requirements at report time. Matching is one-to-one and deterministic:

* duplicates: a claim matches a true duplicate on (target, intent), preferring the same agent, then
  the nearest tick. `dup_precision` = matched claims / claims, `dup_recall` = matched / true duplicates,
  `dup_attr_acc` = matched duplicates whose claimed agent is the true agent, `dup_strict_recall`
  requires the agent in the match.
* false dones: a claim matches a true false done by the same agent (nearest tick).
* agents: per episode, the set of agents a monitor blames against the set the ledger blames
  (`agent_precision`, `agent_recall`), pooled over episodes.
* `episode_dup_acc`: did the monitor say "some duplicate" exactly when there was one.

Pooled rates carry 95% Wilson intervals; F1 values carry a 95% episode-bootstrap interval (2,000
resamples). Aborted episodes are skipped and listed in `summary.json`.

## Running it

```bash
export GEMINI_API_KEY_FILE=<path to the key file>
.venv/bin/python scripts/run_monitor.py runs/pilot/r2 --view report --out results/monitor/pilot-r2-report
.venv/bin/python scripts/run_monitor.py runs/pilot/r2 --view full   --out results/monitor/pilot-r2-full
```

Options: `--monitors llm,baseline`, `--budget` (view tokens), `--limit N` episodes, `--max-calls N` new
LLM calls, `--replay-only` (cache only, no network). The run is resumable: an episode with a result
file for the same model and prompt version is skipped, and a free quota stop exits 3 (rerun later).
Outputs: `episodes/<label>.json` (truth, claims, raw answer, usage, trim info), `episodes.csv` (one row
per episode and monitor), `summary.csv` (long format: monitor, view, group = all / team_model /
condition / shape, metric, k, n, value, CI) and `summary.json`. LLM usage per call is appended to
`runs/usage/monitor_calls.jsonl` (no prompts, no credentials).

## Dev-run results (pilot, `runs/pilot/r2`, 20 episodes, shape F, both team models)

See `results/monitor/pilot-r2-report/` and `results/monitor/pilot-r2-full/`; numbers below.
