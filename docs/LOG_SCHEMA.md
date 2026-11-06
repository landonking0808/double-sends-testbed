# Log schema

Every episode writes one directory:

| File | What it is | Written by |
|---|---|---|
| `episode.jsonl` | every event, one JSON object per line | environment |
| `scheduler.jsonl` | one line per tick: the seeded turn order and agent states | scheduler |
| `ledger.json` | ground truth: every applied side effect, report, delegation, status change | environment |
| `manifest.json` | run settings, code commit, SHA-256 of the three files above | runner |
| `metrics.json` | metrics computed from the ledger and scheduler log (derived, can be regenerated) | `dst.metrics` |

`episode.jsonl`, `scheduler.jsonl` and `ledger.json` are deterministic: keys are sorted, separators are
fixed, and no wall-clock time is written into them. `rerun --replay` reproduces them byte for byte.
`manifest.json` holds the creation time, so it differs between a run and its replay (the replay's
manifest adds `replay_of`).

## `episode.jsonl`

Each line has exactly these keys:

| Key | Type | Meaning |
|---|---|---|
| `episode_id` | string | e.g. `F-B0-scripted-naive-s1` (shape, condition, agents, seed) |
| `seed` | int | episode seed; every random stream is derived from it |
| `t` | int | tick (scheduler round) in which the event happened |
| `agent` | string | the acting agent (`lead`, `m1`, `w3`, ...), or `env` for environment notices |
| `parent` | string or null | that agent's parent in the tree (`null` for the lead and `env`) |
| `event` | string | one of `llm_call`, `tool_call`, `tool_result`, `message`, `report` |
| `payload` | object | event-specific, below |
| `ledger_ref` | string or null | id of the ledger record this event created or refers to (`e0007` effect, `r003` report, `d002` delegation) |

Within a turn the order is always: zero or more `llm_call`, then `tool_call`, then (`report` and/or
`message` produced by the call), then `tool_result`. A `child_stopped` notice (agent `env`) follows the turn that hit the cap.

### Payloads

- **`llm_call`** (LLM agents only): `call_index` (per agent, from 0), `model`, `temperature`, `seed`
  (per call, derived from the episode seed, agent and call index), `prompt_hash` (SHA-256 of the
  JSON-serialized message list, sorted keys), `n_messages`, `response` (the full raw text, or null if the
  client raised), `usage` (`{input_tokens, output_tokens}` if the client reported it, else null),
  `est_tokens` (deterministic chars/4 estimate), `parse_ok`, `error` (client error text or null).
  This is what makes exact replay possible.
- **`tool_call`**: `step` (the agent's turn index, from 0) and `call` (the tool call exactly as the agent
  returned it, `{"tool": ..., "args": {...}}`; malformed calls are kept as given).
- **`tool_result`**: `step`, `tool`, `result` (exactly what the agent saw). Results never contain ledger
  ids; `ledger_ref` on the line does. A `timeout` result still carries the `ledger_ref` of the effect
  that was in fact applied: this is how attribution sees through an ambiguous result.
- **`message`**: an inbox delivery: `to`, `kind` (`delegation`, `report`, `child_stopped`), `from`, `t`,
  and kind-specific fields (`brief`, `item_ids`, `status`, `text`, `claims`, `child`, `reason`).
- **`report`**: every `report` tool call, accepted or not: `report` (id), `status`
  (`done|failed|progress`), `text`, `claims` (list of `{target, intent}` or null), `accepted`, `reason`
  (why the environment refused it under E2, else null).

## `scheduler.jsonl`

One line per tick that had at least one runnable agent:

```json
{"order": ["w3", "lead", "w1"], "status": {"lead": "running", "w1": "running", "w2": "sleeping", ...}, "t": 4, "turns": {"lead": 4, ...}}
```

`order` is the seeded random permutation of the agents that were `running` at the start of the tick;
each gets exactly one turn in that order. `status` is one of `idle` (no work yet), `running`,
`sleeping` (called `noop` with `until: "message"`; woken by the next inbox message), `finished`
(an accepted `done`/`failed` report), `exhausted` (hit the per-agent turn cap). `turns` counts turns
used so far.

## `ledger.json`

The environment-owned ground truth. No agent can write it: agents only receive copies of tool results,
and the only read access is `verify(target)`.

| Key | Content |
|---|---|
| `episode_id`, `seed`, `condition`, `shape`, `task_id`, `config` | episode settings (`config.faults` = fault rates) |
| `batch` | the generated task: `messages` (id, customer, text), `customers` (order data), `policy` |
| `requirements` | machine-checkable requirements: `{id, customer, kind, amount}`; kinds `reply`, `refund`, `no_refund`, `record:status` |
| `agents` | the tree: `{id, role, parent, children}` |
| `items` | message ids assigned to each node (the lead holds all; others via `delegate`) |
| `effects` | every applied side effect: `id`, `seq`, `t`, `agent`, `step`, `tool`, `target`, `intent` (`reply`, `refund`, `record:<field>`), `args`, `returned` (what the agent was told: `ok`, `queued`, `timeout`), `history` (`[[t, status], ...]`, e.g. `queued` then `delivered`/`bounced`), `final`, `key` (E1 idempotency key or null) |
| `suppressed` | E1: calls answered from an earlier effect with the same key (not applied) |
| `blocked` | E1: calls refused because another agent held the target's lease (not applied) |
| `reports` | every report attempt (`accepted`, `reason`, `seq`, `step`) |
| `delegations` | every accepted delegation (`agent`, `child`, `items`, `brief`, `seq`) |
| `status_changes` | every agent state change with its `seq` (used for "was a descendant still running?") |
| `board` | every board post: `n`, `seq`, `t`, `agent`, `kind` (`claim`/`note`), `visible_t` (stale-board fault: one tick later with probability `p_stale_board`) |
| `turns`, `final_status`, `end` | turns per agent, final states, end tick/seq/reason (`quiescent` or `max_ticks`) and unfinished agents |

`seq` is one global counter over every ledger record, so "before" and "after" are exact even within a tick.

## `manifest.json`

`episode_id, seed, shape, condition, agents, provider, model, temperature, history_turns, max_retries,
faults{p_timeout, p_bounce, p_stale_board, delivery_delay}, turn_cap, max_ticks, lease_ticks,
batch{n_messages, n_repeat, task_id}, agent_stats, git_commit, git_dirty_dst, python, platform,
created_at, files_sha256`, and `replay_of` for replays.

## How the metrics use the logs

All metrics come from `ledger.json` and `scheduler.jsonl` (the event log is read only to count LLM
calls, tokens and parse failures). See `dst/metrics.py`:

- duplicates: effective effects (final `delivered` or `applied`) grouped by `(target, intent)`; every
  one after the first is a duplicate, attributed to its `agent` and `step`;
- false done: an accepted `done` report whose node's requirements were not met by the effects with a
  smaller `seq` (judged by their final status);
- premature done: an accepted `done` report by a node with descendants, issued while one of them was
  `running` or `sleeping` according to `status_changes`.
