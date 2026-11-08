---
title: Double Sends and False Dones: A Rerunnable Testbed for Coordination Failures in Delegating LLM Agent Teams
author: Landon King
affiliation: Independent
---

<!--
Source of the sprint report. Build: .venv/bin/python report/build.py --grid grid
Numbers come only from placeholders resolved against results/<grid>/ (see report/build.py).
Monitor numbers ({{M:<view>:...}}) come from results/monitor/ via monitor_views in report/config.json.
Write plainly. Describe what the code does, not what was planned.
-->

# Abstract

Teams of LLM agents that delegate work can fail in two ways their own reports hide: a double send, where an external action is performed twice, and a false done, where an agent reports unfinished work as finished. We release a seeded, rerunnable testbed in which a lead delegates customer-support work down a tree of 7 or 9 agents sharing side-effecting mock tools, faults are injected from the seed, and an environment-owned ledger gives ground truth with exact attribution to agent and step. On two open-weight models, Gemma 4 and Granite 4.0 ({{S:episodes_included}} episodes, all replaying byte for byte), we compare a prompted discipline inside the agents with controls in the environment. An idempotency key removed every duplicate (from {{T1:A:B0:dar}} and {{T1:B:B0:dar}} extra effects per 100 required to {{T1:*:E1:dar_per100|max;.0f}}; Holm-adjusted p {{TT:*:E1,E3:dar_per100:p_holm|max;p}}) by refusing the repeated calls agents still made. The prompt lowered Gemma's rate to {{T1:A:P1:dar}} but did not significantly change Granite's. A read-back gate on done reports removed Granite's premature completions ({{T1:B:B0:pcr_all}} to {{T1:B:E2:pcr_all}}) at {{T1:B:E2:tokens_overhead_vs_b0|signpct}} tokens, yet in every condition {{T1:B:*:fcr_all|min;pct}}-{{T1:B:*:fcr_all|max;pct}} of Granite's and {{T1:A:*:fcr_all|min;pct}}-{{T1:A:*:fcr_all|max;pct}} of Gemma's accepted done reports were false: the gate checks only effects an agent made, so omitted work passes. Catching false completion needs checks against what the task requires.

# 1. Introduction

LLM agents are increasingly organized as delegation trees: a lead splits a goal and hands parts to other agents, which act through tools that change the world (send a message, issue a refund, update a record), while results travel back up as natural-language reports. In a **double send**, two agents, or one agent retrying after an ambiguous tool result, perform the same external action twice, so a customer gets two refunds. In a **false done**, an agent reports its work finished when the world says otherwise (a message bounced, a step was skipped), or a lead reports "all done" while agents below it still work. Each local choice can look reasonable (retry after a timeout; report done when every call returned "ok"), and oversight that reads reports rather than the world sees neither failure.

MAST [@cemri2025] lists related modes ("Step Repetition", "Premature Termination", "No or Incomplete Verification"), and Hammond et al. [@hammond2025] name miscoordination as a key multi-agent risk. The gap between what agents record and what happened is central to METR's investigation of the OpenAI / Hugging Face incident [@metr2026], where agents meant to be isolated coordinated on an unsanctioned message board and some prototyped ways to spoof tool calls in their own transcripts. Our setting is far milder (no agent is adversarial) but isolates the same question: does a control work if it relies on what agents do and say, or only if it relies on what the environment records? Distributed systems put duplicate protection outside the caller, as idempotency keys [@stripe]; for LLM agent teams it is open how much of this instructions can achieve, and at what cost.

Our contributions are:

1. **An open, seeded, rerunnable testbed** ("the office"): a lead delegates customer-support work down a tree of 7 or 9 LLM agents sharing side-effecting mock tools; an environment-owned ledger records every effect; faults are injected from the seed; every episode is logged in one documented format and replays byte for byte.
2. **Ledger-based metrics with exact attribution** of every duplicate and false or premature report to an agent and step, validated with scripted agents against closed forms and by a manual audit of LLM episodes.
3. **A controlled comparison** of a prompted discipline (inside the agents) with environment-enforced idempotency, a read-back gate on done reports, and both, on two open-weight models and two tree shapes. Enforced idempotency removed duplicates for both models, the prompt reduced them for one, and the gate stopped premature reports but not false ones, because it cannot see work never done.
4. **Log-only monitors scored against the ledger** (Track 4): an LLM monitor and a rule baseline read only the agents' communications, and their flags are matched against the ground-truth failures and responsible agents.

# 2. Related Work

**Failures and environments.** MAST [@cemri2025] derives 14 failure modes from annotated traces of 7 multi-agent frameworks; ours fall under its "Task Verification" category and "Step Repetition" mode. τ-bench [@yao2024] checks the final database state against a goal state; TheAgentCompany [@xu2024] and AgentDojo [@debenedetti2024] test mostly single tool-using agents; MultiAgentBench [@zhu2025] compares coordination topologies including trees, GovSim [@piatti2024] studies agents sharing a resource, and Concordia [@concordia] supports agent-based simulation. AutoGen [@wu2023] and CAMEL [@li2023] are the kind of delegating framework our tree imitates. None isolates duplicate side effects and false completion against an effect ledger or compares in-agent and out-of-agent controls on them.

**Attribution, oversight and control.** Zhang et al. [@zhang2025] find failure attribution from logs hard (the best method names the responsible agent in 53.5% of cases); here attribution is exact by construction, so the testbed supplies labelled failures for such methods. AI control [@greenblatt2024] and SHADE-Arena [@kutasov2025] study oversight of agents that may work against it, Chan et al. [@chan2025] argue for infrastructure outside agents, and Lee and Tiwari [@lee2024] show prompt injection spreading between agents. Our environment-side controls are a small, measurable instance of the infrastructure view.

# 3. Methods

**The office** (`dst/env.py`) runs in discrete ticks; in each tick every running agent makes one tool call, in a seeded random order. Agents have 40 turns; an episode ends when no agent runs, after 300 ticks, or at 200 LLM calls. A batch (`dst/tasks.py`) has 12 messages from 9 invented customers, 3 of whom write twice; each customer has an order (amount $20-180, age 1-40 days, an issue), and the policy refunds the full amount if the issue is damaged, not delivered or wrong item, the order is at most 30 days old and at most $150. For every customer the team must deliver a reply, set the record `status` to `resolved`, and refund exactly the amount if eligible, or not at all.

**Agents.** Shape F is a lead with 6 workers; shape D a lead, 2 managers and 3 workers under each. Lead and managers only `delegate` message ids downward and `report` upward; only workers can `send_message`, `issue_refund`, `update_record` and `claim` a customer on a shared board; every role can `read_inbox`, `read_board`, `lookup`, `verify`, `post_board` and `noop`. A done or failed `report` ends the agent's work. **Ledger.** The environment records every applied effect with agent, step, tick, customer, intent, arguments, what the agent was told and delivery history. Agents see only their tool results; `verify(target)` lists every effect on a customer with its status and passes when each kind of effect the caller made there has landed. **Faults** (frozen after a pilot, Appendix D): a side-effecting call returns `timeout` with probability 0.1 although applied; a sent message is `queued` and bounces 2 ticks later with probability 0.15; a board post is visible one tick late with probability 0.5; repeat customers can be split across workers.

Table: The five conditions. B0 and P1 differ only in prompts; E1-E3 change the environment, and their prompts add a short factual description of it (Appendix B). {#tab:conditions}
| Id | Control | Where |
|---|---|---|
| B0 | none: task, role and tool documentation only | baseline |
| P1 | prompted discipline: claim before acting; never repeat a side effect, verify after an ambiguous result; verify every customer before done; no done while delegates still work | agents |
| E1 | idempotency key and lease: each (task, customer, intent) is applied once, a repeat gets the earlier result (resending a bounced message is allowed); an agent acting on or claiming a customer holds it until 6 ticks after its last such call | environment |
| E2 | read-back gate: done is refused unless, for every customer the agent changed, a `verify` after its last change passed, and while any agent below it runs | environment |
| E3 | E1 and E2 together | environment |

The E1 key uses the `task` string the agent passes, and the E2 gate checks only effects the agent made, so an omission (a refund never issued) passes it.

**Models and design.** Model A is `gemma-4-26b-a4b-it` (Gemma 4, mixture of experts, 4B active parameters, Gemini API), model B `@cf/ibm-granite/granite-4.0-h-micro` (IBM Granite 4.0, Cloudflare Workers AI), both free of charge. All agents of a team use one model at temperature 0.7, at most 256 output tokens, one JSON tool call per turn (an unparseable reply is a counted `noop`); prompts differ across conditions only in the condition text (Appendix B). The grid is 5 conditions × 2 shapes × 8 seeds per model, 160 episodes; a seed fixes the batch. Episodes stopped by the provider are rerun from the response cache; those ending at the call or tick limit are kept.

**Metrics** (`dst/metrics.py`, from the ledger). *DAR*: effective side effects beyond the first per (customer, intent), per 100 required effects (a resend after a bounce is not a duplicate), labelled same- or cross-agent. *FCR*: share of accepted done reports whose effects recorded before the report, judged by final status, do not meet every requirement of the customers delegated to that node before the report. *PCR*: share of done reports by leads and managers made while an agent below still ran. *Task success*: share of requirements met at the end. *Cost*: LLM calls, tokens, refused reports, suppressed calls; overhead is relative to B0. The FCR definition was corrected after the grid ran, from a finding of the audit below (it had judged a report against items delegated after it); all tables are recomputed from the logged ledgers (`docs/PROVENANCE.md`).

**Statistics** (`scripts/analyze.py`). DAR is a pooled ratio with a 95% seed-level cluster bootstrap interval; shares carry 95% Wilson intervals, which treat reports within an episode as independent and are too narrow. Each control is compared with B0 by Fisher exact tests on episode-level outcomes (any duplicate, false done, premature done; task incomplete) and a permutation test on per-episode DAR, Holm-corrected across the four controls per model, shape and outcome.

**Validation.** Scripted teams check the code without an LLM: a naive team's DAR stayed within {{V@scripted-demo:scripted-naive:*:B0,P1,E2:dar_z|maxabs;.1f}} standard deviations of its closed form in {{S@scripted-demo:episodes_included}} episodes, and a careful team produced no duplicate or false done (Appendix E). A manual audit of 10 random grid episodes (`docs/AUDIT.md`) re-derived every label from the raw logs with an independent checker.

# 4. Results

::: demo
!include(demo_results.md)
:::

::: nodemo
All {{S:episodes_included}} grid episodes were valid; {{S:hit_call_cap_included}} ended at the call cap, all in shape D ({{EP:A:D:*:hit_call_cap|sum;int}} Gemma, {{EP:B:D:*:hit_call_cap|sum;int}} Granite), and are kept as outcomes. @fig:fail and @tab:main give the failure rates; @fig:cost (Appendix F) plots cost against benefit.

![Failures by condition, shapes pooled (a), and by tree shape (b). DAR: extra effective side effects per 100 required effects, 95% seed-bootstrap interval. False dones: share of accepted done reports, 95% Wilson interval (all roles in (b)). Gemma's lead filed no accepted done report under P1.](figures/{grid}/fig1_failures_by_condition.png, figures/{grid}/fig3_failures_by_shape.png){#fig:fail}

!csv[Results per model (A = Gemma 4, B = Granite 4.0) and condition, shapes pooled, 16 episodes per row. DAR per 100 required effects [95% bootstrap CI]; episodes with a duplicate; FCR and task success with 95% Wilson CI; PCR (k/n); token overhead against B0; refused done reports per episode.](results/{grid}/table1.csv){#tab:main cols=model:model,condition:cond,dar_full:DAR/100,dup_episodes_kn:dup_eps,fcr_all_ci:FCR,pcr_all_kn:PCR,task_success_ci:success,tokens_overhead_vs_b0:tokens:signpct,mean_refused_reports:refused:.1f where=shape=all size=7.5}

**Duplicates.** Without a control, Gemma made {{T1:A:B0:dar_full}} extra effects per 100 required, with a duplicate in {{T1:A:B0:dup_episodes_kn}} episodes, and Granite {{T1:B:B0:dar_full}}, in {{T1:B:B0:dup_episodes_kn}}; {{EP:A:*:B0:dup_cross_agent|sum;int}} of Gemma's {{EP:A:*:B0:dar_extras|sum;int}} and {{EP:B:*:B0:dup_cross_agent|sum;int}} of Granite's {{EP:B:*:B0:dar_extras|sum;int}} B0 duplicates repeated another agent's effect. E1 and E3 brought DAR to {{T1:*:*:E1,E3:dar_per100|max;.1f}} in every cell (Holm-adjusted p {{TT:*:E1,E3:dar_per100:p_holm|max;p}} per model; per shape significant except Granite in shape D, p {{TT:B:D:E1:dar_per100:p_holm|p}}, where B0 had duplicates in {{T1:B:D:B0:dup_episodes_kn}} episodes). The zero is enforced, not learned: under E1 the environment answered {{T1:A:E1:mean_suppressed|.1f}} (Gemma) and {{T1:B:E1:mean_suppressed|.1f}} (Granite) repeated calls per episode with the earlier result. P1 lowered Gemma's DAR to {{T1:A:P1:dar_full}} (p {{TT:A:P1:dar_per100:p_holm|p}}), but {{T1:A:P1:dup_episodes_kn}} episodes still had a duplicate; Granite's went to {{T1:B:P1:dar_full}} (p {{TT:B:P1:dar_per100:p_holm|p}}). E2 alone did not reduce duplicates.

**Premature and false completion.** Granite's leads and managers reported done while agents below still ran in {{T1:B:B0:pcr_all_full}} of such reports in B0 and {{T1:B:P1:pcr_all_kn}} under P1; E2 and E3 refused all of them (episodes with a premature done {{TT:B:E2:any_premature:b0_value|pct}} to {{TT:B:E2:any_premature:control_value|pct}}, p {{TT:B:E2:any_premature:p_holm|p}}). Gemma's leads and managers rarely reported early ({{T1:A:B0:pcr_all_kn}} in B0). False completion stayed common in every condition: FCR was {{T1:B:*:fcr_all|min;pct}}-{{T1:B:*:fcr_all|max;pct}} for Granite and {{T1:A:*:fcr_all|min;pct}}-{{T1:A:*:fcr_all|max;pct}} for Gemma, and all {{T1:B:*:fcr_lead_n|sum;int}} of Granite's accepted lead done reports were false. The largest change, E2 for Gemma ({{T1:A:B0:fcr_all_ci}} to {{T1:A:E2:fcr_all_ci}}; episodes with a false done {{TT:A:E2:any_false_done:b0_value|pct}} to {{TT:A:E2:any_false_done:control_value|pct}}), was not significant after Holm correction (p {{TT:A:E2:any_false_done:p_holm|p}}, unadjusted {{TT:A:E2:any_false_done:p|p}}); for every other control and model it was {{TT:*:P1,E1,E3:any_false_done:p_holm|min;p}} (Granite under E2: {{TT:B:E2:any_false_done:p_holm|p}}).

**Task success and cost.** Gemma met {{T1:A:*:task_success|min;pct}}-{{T1:A:*:task_success|max;pct}} of requirements and Granite {{T1:B:*:task_success|min;pct}}-{{T1:B:*:task_success|max;pct}}; whole batches were rarely finished (Granite {{T1:B:*:task_complete_episodes_k|sum;int}} of {{T1:B:*:task_complete_episodes_n|sum;int}} episodes, Gemma {{T1:A:*:task_complete_episodes_k|sum;int}}), and no control changed the share of incomplete episodes significantly. Gemma used {{T1:A:B0:mean_tokens|,.0f}} tokens per B0 episode, Granite {{T1:B:B0:mean_tokens|,.0f}}. Every control cost Gemma {{T1:A:P1,E1,E2,E3:tokens_overhead_vs_b0|min;signpct}} to {{T1:A:P1,E1,E2,E3:tokens_overhead_vs_b0|max;signpct}} tokens; for Granite E1 cost {{T1:B:E1:tokens_overhead_vs_b0|signpct}} and P1 {{T1:B:P1:tokens_overhead_vs_b0|signpct}}, but E2 and E3 cost {{T1:B:E2:tokens_overhead_vs_b0|signpct}} and {{T1:B:E3:tokens_overhead_vs_b0|signpct}}, with {{T1:B:E2:mean_refused_reports|.1f}} and {{T1:B:E3:mean_refused_reports|.1f}} refused reports per episode. Shape intervals overlap for most rates (@fig:fail, panel b).

**Audit and replay.** In the audit the computed labels agreed with the hand reading in every episode after the FCR fix, which addressed the only disagreement; every ledger effect matched its tool call. Two cases show the mechanisms. In Gemma episode D-E2-s4 a `send_message` returned `timeout` although delivered; the worker kept looking up and resending without `verify`, and the customer received seven replies, while the gate correctly accepted the worker's later done. In Granite episode F-E2-s1 a worker holding an eligible customer only marked the record resolved, verified that one effect and reported done; the gate accepted it, and the customer got no reply and no refund. Replaying all {{S:episodes_included}} episodes from their recorded responses reproduced each episode's logs byte for byte.

## Log-only monitoring (Track 4)

We gave an LLM monitor (`gemma-4-31b-it`, temperature 0) and a rule baseline the *report view* of each episode (delegations, reports, board posts; no tool calls, results or ledger; `docs/MONITOR.md`) and matched their flags one to one against the ledger's {{M:report:llm:all:dup_recall:n}} duplicates and {{M:report:llm:all:fd_recall:n}} false dones. The LLM monitor found {{M:report:llm:all:dup_recall:full}} of the duplicates (precision {{M:report:llm:all:dup_precision:ci}}) and named the right agent for {{M:report:llm:all:dup_attr_acc:kn}} of those; a rule flagging an effect claimed in two workers' reports found {{M:report:baseline:all:dup_recall:full}}. A worker that repeats its own call and reports the effect once leaves no trace in this view. Flagging every done report finds all false dones by construction, at precision {{M:report:baseline:all:fd_precision:ci}}; the LLM monitor had precision {{M:report:llm:all:fd_precision:ci}} and recall {{M:report:llm:all:fd_recall:ci}}.
<!-- Full view: uncomment when results/monitor/grid-full is finished.
With the *full view*, which adds every tool call and the result the agent saw, the LLM monitor found {{M:full:llm:all:dup_recall:full}} of the duplicates (precision {{M:full:llm:all:dup_precision:ci}}) and the repeat-call rule {{M:full:baseline:all:dup_recall:full}}; for false dones the LLM monitor's precision was {{M:full:llm:all:fd_precision:ci}} and recall {{M:full:llm:all:fd_recall:ci}}.
-->
:::

# 5. Discussion and Limitations

**Controls outside the agents removed what they could see.** The idempotency key removed duplicates for both models at small cost, but by refusing calls the agents still made: a zero duplicate rate under enforcement says nothing about whether agents became more careful. The prompted discipline worked partly, and only for Gemma, the larger model. For duplicate side effects, these results favour enforcement where the effect happens over instructing the agent that causes it.

**Gates see commission, not omission.** The read-back gate enforced the structural part of completion (no done while delegates run) and removed Granite's premature reports, but a report that omits work passes a check of the agent's own effects. A gate that knows each delegated item's requirements, as this environment does, could refuse those reports too; in deployed systems that means checking reports against the task specification, not the agent's action log. For the weaker model the gate was also expensive: refusals were often repeated rather than acted on, although each named what was missing (a customer to verify, or the agents still running).

**Rates must be read together.** Granite duplicated less than Gemma while also doing less (lower task success, earlier reports), and fewer effects leave fewer chances to repeat one. A failure rate can improve while the team gets worse at the task, so we report duplicates, completion, success and cost side by side. For oversight, a retry by one agent and work silently skipped are absent from reports by construction, so ledger-level checks or full tool logs are needed to see them.

**Limitations.** Tasks are synthetic and tools are mocks, so absolute rates follow from fault rates we chose. We tested two small open-weight models; stronger models may follow the prompt better. With 8 seeds per cell intervals are wide, and episode-level tests have little power, so a non-significant result (P1 for Granite) is not evidence of no effect. {{EP:A:D:*:hit_call_cap|sum;int}} Gemma shape-D episodes ended at the call cap, counting truncated work as failure. The FCR definition was corrected after the grid. Live reruns differ because provider sampling is not deterministic; exact reproduction is by replay. Our agents are cooperative, the E1 key depends on an agent-supplied string, and the monitors used one model and a token-trimmed view. Appendix A gives more detail.

**Future work.** A requirement-aware completion gate, which this testbed can evaluate directly; agents that try to pass the gates or hide repeats from monitors; larger trees (a 19-agent shape exists); real coordination protocols instead of the shared board; and larger models on a subset of conditions.

# 6. Conclusion

In an open testbed with ground truth from an environment-owned ledger and exact replay, delegating LLM teams made duplicate side effects and false completion reports at measurable rates. Enforcement in the environment removed duplicates and premature reports where prompting did not reliably do so, but no control we tested stopped agents from reporting incomplete work as done: the gate checked what agents did, and the failure was in what they did not do. The testbed, logs and analysis are released so other controls, models and monitors can be compared on the same ground truth.

# Code and Data

Code: [https://github.com/landonking0808/double-sends-testbed](https://github.com/landonking0808/double-sends-testbed) (environment, agents, analysis, monitors, tests, report build). Data: all episode logs under `runs/` (Appendix C), tables under `results/`, the audit in `docs/AUDIT.md` and `docs/audit/`, monitor results in `results/monitor/`. `scripts/analyze.py` recomputes every table from the logs, `scripts/replay_check.py runs/grid/A runs/grid/B --n 160` replays every grid episode with no API call and checks its logs byte for byte, and the build logs each number in this report with its source row (`report/out/numbers.csv`).

**Prior work statement.** None. All code, experiments and text were produced during the sprint, November 6-8, 2026.

# References

!references

# Appendix A. Limitations and Dual-Use / Ethical Considerations

**What the results establish.** Within this environment, for these two models, tree shapes and fault rates: the measured rates of duplicate actions and of false or premature completion under each control, with their uncertainty and cost, and how much of each failure two log-only monitors recover. **What they do not establish.** Rates in any real deployment; the behavior of larger or differently trained models; robustness of the controls against agents that try to evade them; or that the environment's simplifications (a shared board, a single batch, mock tools without real latency) preserve the dynamics of production systems. The validation shows that the metric code computes what it claims on scripted agents, and the audit that its labels match a hand reading on a sample of LLM episodes; neither shows that our requirements capture everything a user would count as a failure. The episode-level tests have little power at 16 episodes per condition, so a non-significant difference (for example P1 for Granite) is not evidence of no effect. The zero duplicate rate under E1 and E3 holds only for repeats with the same `task` string; an agent that varied it would not be caught.

**Dual use.** The testbed measures reliability failures of cooperative agents in mock tools. It adds no capability for agents to coordinate covertly, to evade monitoring, or to act on real systems; all customers and orders are invented, and no real message, payment or record is touched. The controls we study are defensive. A map of where controls fail could in principle help someone design agent behavior that slips past similar checks (for example, by varying the idempotency `task` string, by omitting work that a read-back gate cannot see, or by keeping repeats out of the reports a monitor reads). We state these gaps openly because they are properties of simple, widely used patterns, and because publishing them alongside the controls helps defenders more than it helps an attacker who already controls an agent. The released logs contain only synthetic data and model outputs about it. The monitor results also bear on a broader risk: oversight that reads only what agents report can certify work that did not happen, and our numbers should not be read as showing that any monitor is sufficient.

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

An `llm_call` payload holds the call index, model, temperature, per-call seed, the SHA-256 of the full prompt, the raw response, provider-reported token counts, whether the reply parsed, and any client error. A replay serves the recorded responses in order and stops with an error if a prompt's hash differs from the recorded one, so a replay either reproduces the episode exactly or fails. The `metrics.json` files in the grid's run directories were written before the definition fix of Section 3; the analysis recomputes metrics from the ledgers.

# Appendix D. Pilot

A pilot on shape F (2 seeds per condition and model, two rounds; `docs/PILOT.md`, logs in `runs/pilot/`) found that with a timeout probability of 0.2 every B0 episode contained a duplicate, which leaves no room to compare controls on that measure. We lowered it to 0.1 and froze all settings before the grid. The pilot also showed agents re-sending after a `queued` result, so the tool description now says that `queued` means accepted for delivery (the same text in every condition; delivery is still visible only through `verify`). Pilot episodes are not part of the results.

# Appendix E. Validation with Scripted Agents

Two scripted policies (`dst/agents/scripted.py`) check the environment and metric code without an LLM. The *naive* team splits messages round-robin, never reads the board or verifies, retries after every `timeout` (up to 3 times), and its lead reports done right after delegating; its expected duplicates and false lead reports have closed forms (`dst/analytic.py`). The *careful* team claims customers, verifies instead of retrying, resends bounced messages and reports done only when `verify` shows every requirement met. In {{S@scripted-demo:episodes_included}} scripted episodes ({{V@scripted-demo:scripted-naive:F:B0:n_episodes}} seeds for each policy, shape (F, D and a 19-agent tree) and condition, at the pre-pilot timeout probability {{V@scripted-demo:scripted-naive:F:B0:faults|ptimeout}}), the naive team's measured DAR stayed within {{V@scripted-demo:scripted-naive:*:B0,P1,E2:dar_z|maxabs;.1f}} standard deviations of the analytic expectation in every cell without idempotency, its DAR was {{V@scripted-demo:scripted-naive:*:E1,E3:dar_measured_per100|max;.0f}} wherever the key was enforced, and the careful team produced {{T1@scripted-demo:scripted-careful:*:dar_per100|max;.0f}} duplicates and {{T1@scripted-demo:scripted-careful:*:fcr_all_k|sum;int}} false dones in every condition. The same checks run as unit tests (`tests/test_metrics_analytic.py`, which also requires |z| ≤ 4 and shows the test has power against a wrong model).

# Appendix F. Cost Against Benefit

![Cost against benefit of each control, per model, shapes pooled: token overhead against B0 (x) and the reduction in DAR (top) and in FCR (bottom) against B0.](figures/{grid}/fig2_cost_vs_reduction.png){#fig:cost width=4.2}

# LLM Usage Statement

LLM coding and writing assistants helped write and debug the testbed code and edit drafts of this report. The experiments use the two models named in Section 3, and the monitor uses a third open-weight model named in Section 4. All results were produced by the released code and checked against the episode logs; every number in the text is filled in by the build script from tables computed from those logs. Every reference was checked against its live page (title, authors, year, link); the record of that check is in the repository (`report/REFERENCES_CHECK.md`).
