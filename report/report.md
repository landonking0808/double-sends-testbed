---
title: Double Sends and False Dones: A Rerunnable Testbed for Coordination Failures in Delegating LLM Agent Teams
author: Landon King
affiliation: Independent
---

<!--
Source of the sprint report. Build: .venv/bin/python report/build.py --grid grid
Numbers come only from placeholders resolved against results/<grid>/ (see report/build.py).
Write plainly. Describe what the code does, not what was planned.
-->

# Abstract

TODO: abstract (150-250 words), written last from the final grid numbers: the problem (duplicate side effects and false or premature "done" reports in delegating agent teams), the testbed (seeded, rerunnable, ground-truth effect ledger, injected faults), the comparison (prompted discipline versus environment-enforced idempotency and read-back gates, two open-weight models, two tree shapes), the headline numbers with intervals, and the takeaway.

# 1. Introduction

LLM agents are increasingly organized as delegation trees: a lead splits a goal, hands parts to other agents, and those agents act through tools that change the world (send a message, issue a refund, update a record), while results travel back up as natural-language reports. Two failures in such teams are cheap to cause and costly in deployment. In a **double send**, two agents, or one agent retrying after an ambiguous tool result, perform the same external action twice, so a customer gets two refunds. In a **false done**, an agent reports its work finished when the world says otherwise (a message bounced, a step was skipped), or a lead reports "all done" while agents below it are still working. Each agent's local choice can look reasonable (retry after a timeout; report done when every call returned "ok") while the team acts twice or misreports, and oversight that reads reports rather than the world sees neither failure.

MAST [@cemri2025] lists related modes ("Step Repetition", "Premature Termination", "No or Incomplete Verification"), and Hammond et al. [@hammond2025] name miscoordination as one of three key multi-agent failure modes. The gap between what agents record and what happened is also central to METR's investigation of the OpenAI / Hugging Face incident [@metr2026], in which about 1,200 agents meant to be isolated coordinated on an unsanctioned message board and some prototyped ways to spoof the tool calls in their own transcripts. Our setting is far milder (no agent is adversarial), but it isolates the same question: does a control work if it relies on what agents do and say, or only if it relies on what the environment records? Distributed systems put duplicate protection outside the caller, as idempotency keys that let a server recognize a retried request [@stripe]; for LLM agent teams it is open how much of this can be had by instructing the agents, and at what cost.

Our main contributions are:

1. **An open, seeded, rerunnable testbed** ("the office"): a lead delegates customer-support work down a tree of 7 or 9 LLM agents that share side-effecting mock tools; an environment-owned ledger, which agents cannot write, records every effect; faults are injected from the seed; every episode is logged in one documented format and replays byte for byte.
2. **Ledger-based metrics with exact attribution**: duplicate actions, false and premature completion, task success, report fidelity and cost, computed from the ledger rather than by an LLM judge, with every failure attributed to an agent and step, and validated against closed-form rates with scripted agents.
3. **A controlled comparison** of a prompted discipline (inside the agents) with environment-enforced idempotency, a read-back gate on "done" reports, and both (outside the agents), on two open-weight models from different families and two tree shapes. TODO: one sentence with the headline finding.

# 2. Related Work

**Failure taxonomies.** MAST [@cemri2025] derives 14 failure modes in 3 categories from annotated traces of 7 multi-agent frameworks; our failures fall under its "Task Verification" category and its "Step Repetition" mode. Hammond et al. [@hammond2025] frame multi-agent risk as miscoordination, conflict and collusion. We build an environment in which two such failures occur at measurable rates with ground truth.

**Environments with state-based checking.** τ-bench [@yao2024] compares the final database state with an annotated goal state; TheAgentCompany [@xu2024] and AgentDojo [@debenedetti2024] test tool-using agents in simulated workplaces and under prompt injection, mostly with single agents. MultiAgentBench [@zhu2025] compares coordination topologies including trees, GovSim [@piatti2024] studies LLM agents sharing a resource, and Concordia [@concordia] supports generative agent-based simulation; AutoGen [@wu2023] and CAMEL [@li2023] are the kind of delegating framework our tree imitates. None isolates duplicate side effects and false completion against an effect ledger, or compares in-agent and out-of-agent controls on them.

**Attribution, oversight and control.** Zhang et al. [@zhang2025] find automated failure attribution hard from logs: their best method names the responsible agent in 53.5% of cases and the decisive step in 14.2%. Here attribution is exact by construction, so the testbed can supply labelled failures for such methods. AI control [@greenblatt2024] and SHADE-Arena [@kutasov2025] study oversight of agents that may work against it, Chan et al. [@chan2025] argue for infrastructure outside agents that shapes their interactions, and Lee and Tiwari [@lee2024] show prompt injection spreading between agents. Our environment-side controls are a small, measurable instance of the infrastructure view.

# 3. Methods

## The office

The environment (`dst/env.py`) is a single Python process with discrete time. In every tick each running agent takes one turn, in a seeded random order; a turn is one tool call. Agents have 40 turns each; an episode ends when no agent is running, after 300 ticks, or when the team has used 200 LLM calls. A global sequence number on every ledger record makes "before" and "after" exact within a tick.

**Task.** A batch is generated from the seed (`dst/tasks.py`): 12 messages from 9 invented customers, 3 of whom write twice. Each customer has an order with an amount ($20-180), an age (1-40 days) and an issue; the policy given to the agents refunds the full amount if and only if the issue is damaged, not delivered or wrong item, the order is at most 30 days old and at most $150. For every customer the team must deliver a reply, set the record `status` to `resolved`, and refund exactly the amount if eligible, or not at all. These requirements are checked against the ledger.

**Agents and trees.** Shape F is a lead with 6 workers (7 agents); shape D a lead, 2 managers and 3 workers under each (9 agents). The lead receives the task and all messages. Lead and managers have no side-effecting tools: they `delegate` message ids to the agents directly below them and `report` to their parent. Only workers can `send_message`, `issue_refund` and `update_record`, and `claim` a customer on a shared board; every role can `read_inbox`, `read_board`, `lookup` an order, `verify`, `post_board`, `report` and `noop` (optionally sleeping until a message arrives). A done or failed `report` ends the agent's work and goes to its parent's inbox.

**Ledger.** Every applied side effect is recorded by the environment with its agent, step, tick, customer, intent (`reply`, `refund`, `record:<field>`), arguments, what the agent was told and its delivery history. Agents see only copies of tool results; their one read path into the ledger is `verify(target)`, which lists every effect on that customer with its current status and passes when each kind of effect the caller made there has landed and none is pending.

**Faults** (frozen in `configs/frozen.json` after a pilot, Appendix D). (a) *Timeout-but-applied*: a side-effecting call returns `timeout` with probability 0.1 although the effect was applied. (b) *Accepted-then-bounced*: `send_message` returns `queued`, and 2 ticks later the message is delivered or, with probability 0.15, bounced. (c) *Stale board*: a board post becomes visible to others one tick late with probability 0.5. (d) *Overlap*: with 3 repeat customers, a split by message can give two workers the same customer. All draws come from streams derived from the episode seed.

## Conditions

Table: The five conditions. B0 and P1 differ only in the agents' prompts; E1-E3 change what the environment does, and their prompts add a short factual description of that behavior (Appendix B). {#tab:conditions}
| Id | Control | Where it acts |
|---|---|---|
| B0 | none: task, role and tool documentation only | baseline |
| P1 | prompted discipline: claim before acting; never repeat a side effect, verify after an ambiguous result; verify every customer before done; no done while delegates still work | inside the agents |
| E1 | idempotency key and lease: each (task, customer, intent) is applied once, a repeat gets the earlier result (resending a bounced message is allowed); an agent that acts on or claims a customer holds it until 6 ticks after its last such call, refusing other agents' side effects and claims there | environment |
| E2 | read-back gate: done is refused unless, for every customer the agent changed, a `verify` after its last change there passed; and refused while any agent below it is running | environment |
| E3 | E1 and E2 together | environment |

Two properties matter for interpretation: the E1 key is built from the `task` string the agent passes, so a different string gets a different key; and the E2 gate checks only effects the agent made, so an omission (a refund never issued) passes it.

## Models and prompting

We use two open-weight models from different families, both free of charge (limits in `docs/MODELS.md`): **model A**, `gemma-4-26b-a4b-it` (Gemma 4, a mixture of experts with 26B total and 4B active parameters) through the Gemini API, and **model B**, `@cf/ibm-granite/granite-4.0-h-micro` (IBM Granite 4.0) through Cloudflare Workers AI. All agents of a team use the same model at temperature 0.7 with at most 256 output tokens (for model A, Gemma's thinking level is set to "minimal"). Each turn an agent answers with one JSON tool call; a reply that cannot be parsed becomes a counted `noop`. Prompts (system prompt, the agent's brief, a one-line log of its own earlier steps, recent exchanges within about 1,200 tokens, and the current observation) are identical across conditions except for the condition text, and never name the condition; full prompts and construction are in Appendix B.

**Design.** The grid is 5 conditions × 2 shapes × 8 seeds (1-8) per model, 160 episodes. A seed fixes the batch, so all cells see the same customers; fault draws follow the order of calls, so realized faults differ once agents act differently. An episode stopped by the provider (quota, or a call failing after retries) is not a valid sample and is rerun from the response cache; episodes ending at the tick or call limit are kept and flagged.

## Metrics

All metrics come from the ledger and scheduler log (`dst/metrics.py`); the event log is read only for call and token counts.

- **Duplicate action rate (DAR)**: effective side effects (final status delivered or applied) beyond the first per (customer, intent), per 100 required effects; a resend after a bounce is not a duplicate. Each duplicate is labelled *same agent* or *cross agent*; we also report the share of episodes with any duplicate.
- **False completion rate (FCR)**: the share of accepted done reports for which the effects recorded before the report, judged by their final status, do not meet every requirement of the node's customers (a send that bounces later makes it false); per role and pooled.
- **Premature completion rate (PCR)**: the share of accepted done reports by a lead or manager made while an agent below it was still running.
- **Task success**: the share of requirements met at the end. **Report fidelity**: precision (claimed effects that had landed) and recall (met requirements claimed) of done reports; a report without claims claims all of the node's requirements.
- **Cost**: turns, LLM calls, provider-reported tokens, parse failures, refused reports, and calls suppressed or blocked under E1; overhead is relative to B0 in the same model and shape.

## Statistics

The analysis (`scripts/analyze.py`) pools episodes per model, shape and condition, and over shapes. DAR per 100 is a pooled ratio with a 95% interval from a seed-level cluster bootstrap (5,000 resamples); shares are pooled counts with 95% Wilson intervals. Each control is compared with B0 by two-sided Fisher exact tests on episode-level outcomes (any duplicate, false done, premature done; task incomplete) and a permutation test on per-episode DAR (10,000 permutations, within shape when shapes are pooled), with Holm's correction across the four controls per model, shape and outcome. Resampling is seeded, so the tables are byte-identical for the same logs.

## Validation

Two scripted policies (`dst/agents/scripted.py`) check the environment and metric code without an LLM. The *naive* team splits messages round-robin, never reads the board or verifies, retries after every `timeout` (up to 3 times), and its lead reports done right after delegating; its expected duplicates and false lead reports have closed forms (`dst/analytic.py`). The *careful* team claims customers, verifies instead of retrying, resends bounced messages and reports done only when `verify` shows every requirement met. In {{S@scripted-demo:episodes_included}} scripted episodes ({{V@scripted-demo:scripted-naive:F:B0:n_episodes}} seeds for each policy, shape (F, D and a 19-agent tree) and condition, at the pre-pilot timeout probability {{V@scripted-demo:scripted-naive:F:B0:faults|ptimeout}}), the naive team's measured DAR stayed within {{V@scripted-demo:scripted-naive:*:B0,P1,E2:dar_z|maxabs;.1f}} standard deviations of the analytic expectation in every cell without idempotency, its DAR was {{V@scripted-demo:scripted-naive:*:E1,E3:dar_measured_per100|max;.0f}} wherever the key was enforced, and the careful team produced {{T1@scripted-demo:scripted-careful:*:dar_per100|max;.0f}} duplicates and {{T1@scripted-demo:scripted-careful:*:fcr_all_k|sum;int}} false dones in every condition. The same checks run as unit tests (`tests/test_metrics_analytic.py`, which also requires |z| ≤ 4 and shows the test has power against a wrong model). TODO: manual audit result (agreement of the computed labels with a hand reading of 10 random LLM episodes, `scripts/audit_sample.py`) and replay check of 3 random grid episodes (`scripts/replay_check.py`).

# 4. Results

::: demo
!include(demo_results.md)
:::

::: nodemo
TODO: Results from results/grid only, every number a placeholder (for example the DAR of model A in B0). Planned order: Figure 1 (DAR and FCR by condition per model, with intervals); Table 1 (all metrics per model and condition, shapes pooled, from table1.csv); the B0-versus-control tests with Holm-adjusted p-values; Figure 2 (cost overhead against reduction in failures); shape F versus D (Figure 3); same-agent versus cross-agent duplicates; aborted and flagged episodes. Observations first, interpretation in Section 5.
:::

# 5. Discussion and Limitations

TODO: what the results mean for oversight of delegating teams: whether instructions inside the agents closed the gap or only controls outside them did, at what cost, and how the two models differed. Keep observations (Section 4) separate from interpretation.

One asymmetry frames the comparison: idempotency (E1) can only remove duplicates and the gate (E2) can only refuse reports, so their effect on task success runs through how agents react to suppressed calls and refusals, while the prompted discipline (P1) can address both failures but only as far as the model follows it.

## Limitations

Tasks are synthetic and tools are mocks, so absolute rates follow from fault rates we chose (changed once, in the pilot) and say nothing about deployed systems. We tested two small open-weight models on free tiers; stronger models may follow the prompted discipline better, so the P1 result is specific to these models. With 8 seeds per cell intervals are wide. Provider sampling is not fully deterministic: a live rerun gives a different but valid episode, and exact reproduction is by replay. The E1 key relies on an agent-supplied `task` string and the E2 gate cannot see omissions, both simplifications of real infrastructure. Our agents are cooperative; we did not test agents that try to evade the controls. The metrics depend on our requirement definitions (for example, a second identical record update counts as a duplicate). Appendix A states what the results do and do not establish.

## Future Work

Agents that try to pass the gates (Track 4); larger trees (a 19-agent shape exists in the code); log-only monitors scored against the exact attribution; real coordination protocols instead of the shared board; and a larger model on a subset of conditions, to test how far small models stand in for frontier agents here.

# 6. Conclusion

TODO: one or two paragraphs from the final results.

# Code and Data

- **Code repository**: [https://github.com/landonking0808/double-sends-testbed](https://github.com/landonking0808/double-sends-testbed) (environment, agents, provider clients, analysis, tests, report build).
- **Data**: all episode logs (grid, pilot, scripted validation) under `runs/` (format in Appendix C); analysis tables under `results/`.
- **Reproduce**: `python scripts/analyze.py runs/grid/A runs/grid/B --out results/grid` recomputes every table from the logs; `python -m dst.run rerun --replay <episode dir>` replays an episode from its recorded responses, with no API call, and checks its logs are byte-identical.

**Prior work statement.** None. All code, experiments and text of this project were produced during the sprint, November 6-8, 2026; no code was written before it.

# References

!references

# Appendix A. Limitations and Dual-Use / Ethical Considerations

**What the results establish.** Within this environment, for these two models, tree shapes and fault rates: the measured rates of duplicate actions and of false or premature completion under each control, with their uncertainty and cost. **What they do not establish.** Rates in any real deployment; the behavior of larger or differently trained models; robustness of the controls against agents that try to evade them; or that the environment's simplifications (a shared board, a single batch, mock tools without real latency) preserve the dynamics of production systems. The validation shows that the metric code computes what it claims on scripted agents; it does not show that our requirements capture everything a user would count as a failure.

**Dual use.** The testbed measures reliability failures of cooperative agents in mock tools. It adds no capability for agents to coordinate covertly, to evade monitoring, or to act on real systems; all customers and orders are invented, and no real message, payment or record is touched. The controls we study are defensive. A map of where controls fail could in principle help someone design agent behavior that slips past similar checks (for example, by varying the idempotency `task` string, or by omitting work that a read-back gate cannot see). We state these gaps openly because they are properties of simple, widely used patterns, and because publishing them alongside the controls helps defenders more than it helps an attacker who already controls an agent. The released logs contain only synthetic data and model outputs about it.

**Ethics.** No human subjects, personal data or paid services were involved. All model calls used free tiers within their published limits.

# Appendix B. Agent Prompts

**Prompt construction** (`dst/agents/llm_agent.py`). Each call sends the system prompt; the first exchange, which holds the agent's brief; and a user message that starts with the agent's log ("Your log so far (turn: what happened):", one line per earlier call with its key arguments and the gist of its result, and per delegation or report received, at most 40 lines), followed by "Turn n (k turns left).", the new messages, the agent's last call and its result as compact JSON (cut at 1,000 characters), and "Reply with one JSON tool call." Between the first exchange and the current message, the newest full exchanges are kept while the estimated prompt stays under about 1,200 tokens (at most 3). Code fences and prose around the JSON are tolerated; only the first call in a reply is executed. Each call's sampling seed is derived from the episode seed, the agent and the call index.

!prompts

# Appendix C. Log Schema

Every episode writes one directory with five files (full schema in `docs/LOG_SCHEMA.md`). `episode.jsonl`, `scheduler.jsonl` and `ledger.json` contain no wall-clock time and are written with sorted keys, so a replay reproduces them byte for byte.

Table: Files of one episode directory. {#tab:files}
| File | Content |
|---|---|
| `episode.jsonl` | every event, one JSON object per line: `episode_id`, `seed`, `t` (tick), `agent`, `parent`, `event` (`llm_call`, `tool_call`, `tool_result`, `message`, `report`), `payload`, `ledger_ref` |
| `scheduler.jsonl` | one line per tick: the seeded turn order, every agent's state (idle, running, sleeping, finished, exhausted) and turns used |
| `ledger.json` | ground truth: the generated batch and requirements, the tree, items assigned to each node, every applied effect (agent, step, tick, target, intent, arguments, what the agent was told, delivery history, final status, idempotency key), suppressed and blocked calls (E1), every report attempt (accepted or refused, with the reason), delegations, state changes, board posts with their visibility tick, end reason |
| `manifest.json` | settings (shape, condition, seed, model, temperature, fault rates, limits), provider usage counters, code commit, creation time, SHA-256 of the three files above |
| `metrics.json` | the metrics of Section 3, derived from the files above (can be regenerated) |

An `llm_call` payload holds the call index, model, temperature, per-call seed, the SHA-256 of the full prompt, the raw response, provider-reported token counts, whether the reply parsed, and any client error. A replay serves the recorded responses in order and stops with an error if a prompt's hash differs from the recorded one, so a replay either reproduces the episode exactly or fails.

# Appendix D. Pilot

A pilot on shape F (2 seeds per condition and model, two rounds; `docs/PILOT.md`, logs in `runs/pilot/`) found that with a timeout probability of 0.2 every B0 episode contained a duplicate, which leaves no room to compare controls on that measure. We lowered it to 0.1 and froze all settings before the grid. The pilot also showed agents re-sending after a `queued` result, so the tool description now says that `queued` means accepted for delivery (the same text in every condition; delivery is still visible only through `verify`). Pilot episodes are not part of the results.

# LLM Usage Statement

LLM coding and writing assistants helped write and debug the testbed code and edit drafts of this report. The experiments use the two models named in Section 3. All results were produced by the released code and checked against the episode logs; every number in the text is filled in by the build script from tables computed from those logs. Every reference was checked against its live page (title, authors, year, link); the record of that check is in the repository (`report/REFERENCES_CHECK.md`).
