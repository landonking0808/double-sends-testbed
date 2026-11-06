# Pilot (Fri Nov 6 2026) and the frozen settings

Times are US Central (CST), read from the run logs (`grid_manifest.json` invocations, `manifest.json`
`created_at`). Logs: `runs/pilot/` (one directory per episode, `docs/LOG_SCHEMA.md`); the table below is
`python scripts/pilot_table.py runs/pilot/r2/A runs/pilot/r2/B --md`. n = 2 seeds per cell: these numbers
only check that the setup is in a usable range; they are not results.

## What was piloted

Shape F (lead + 6 workers), conditions B0, P1, E1, E2, E3, seeds 1-2, 12 customer messages (9 customers,
3 write twice), temperature 0.7.

* **Model A**: Gemini API `gemma-4-26b-a4b-it` (free tier).
* **Model B**: Workers AI `@cf/ibm-granite/granite-4.0-h-micro` (free allocation).

Round 1 (`runs/pilot/r1/`, 8:35-10:27 AM) used the default fault rates `p_timeout 0.2, p_bounce 0.15,
p_stale_board 0.5, delivery_delay 2`. Round 2 (`runs/pilot/r2/`, 10:28 AM-1:08 PM) changed one rate,
`p_timeout 0.2 -> 0.1`, plus one line of tool documentation (below). A shape-D check on model B
(`runs/pilot/r2-D/B/`, B0 and E3, 1:10-1:17 PM) used the round-2 settings.

## Round 1 (p_timeout 0.2)

| model | cond | n | episodes with a duplicate | DAR / 100 required | false dones | task success |
|---|---|---|---|---|---|---|
| B | B0 | 2 | 2/2 | 8.9 | 10/12 | 54% |
| B | P1 | 2 | 2/2 | 11.1 | 6/13 | 57% |
| B | E1 | 2 | 0/2 | 0.0 | 12/12 | 50% |
| B | E2 | 2 | 1/2 | 2.2 | 7/8 | 35% |
| B | E3 | 2 | 0/2 | 0.0 | 7/10 | 46% |
| A | B0 | 1 finished | 1/1 | 30.4 (7 extra of 23) | 3/6 | 93% |

Model A's other nine round-1 episodes were stopped unfinished when round 2 started; every one that had
reached its side effects already had duplicates in B0 (s2: 2 at 116 calls) and P1 (s1: 4). Their partial
logs are kept (`manifest.json` says `aborted: true`, end reason `aborted_deadline`). In model A, 5 of the
7 B0-s1 duplicates were retries right after a `timeout` result (the intended timeout-but-applied fault).

**Reading**: B0 was at the ceiling of the episode-level measure (every B0 episode of both models had a
duplicate, 3/3) and model A's B0 DAR was about 30 per 100, leaving little room to tell the controls apart
on the share of episodes with a duplicate. Long retry chains also made model A's episodes longer.

## The one change, and why

* **`p_timeout` 0.2 -> 0.1.** Duplicates in A come mainly from retry-after-timeout, so this is the rate
  that moves B0. All other rates unchanged (`p_bounce 0.15` keeps false dones possible; stale board 0.5
  and 3 repeat customers keep cross-agent overlap possible).
* **Tool documentation.** Round 1 showed agents resending after a `queued` result, i.e. reading the
  acceptance status as "not sent". `send_message` now documents "the result queued means accepted for
  delivery" (API documentation, identical in every condition; no advice about retries or checking).
  Delivery is still only visible through `verify`, and bounces still happen.

## Round 2 (frozen settings), per model and condition

Calls, tokens and parse failures are from the episode logs (provider-reported tokens on every call).

| model | cond | n | episodes w/ dup | DAR/100 | FCR (false/accepted done) | PCR | task success | LLM calls/ep | input tok/ep | output tok/ep | parse failures |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | B0 | 2 | 2/2 | 6.7 | 6/14 | 0/2 | 49/54 (91%) | 92 | 115,145 | 3,376 | 0/184 |
| A | P1 | 2 | 2/2 | 13.3 | 1/9 | 0/0 | 48/54 (89%) | 133 | 184,968 | 3,900 | 1/266 |
| A | E1 | 2 | 0/2 | 0.0 | 10/16 | 0/2 | 42/54 (78%) | 92 | 120,010 | 3,214 | 0/183 |
| A | E2 | 2 | 2/2 | 51.1 | 3/12 | 0/0 | 50/54 (93%) | 148 | 208,362 | 4,710 | 0/297 |
| A | E3 | 2 | 0/2 | 0.0 | 6/14 | 0/2 | 50/54 (93%) | 123 | 173,416 | 4,274 | 0/246 |
| B | B0 | 2 | 1/2 | 11.1 | 13/13 | 2/2 | 24/54 (44%) | 50 | 49,010 | 2,572 | 0/100 |
| B | P1 | 2 | 1/2 | 2.2 | 11/14 | 2/2 | 30/54 (56%) | 54 | 54,346 | 2,512 | 0/107 |
| B | E1 | 2 | 0/2 | 0.0 | 12/13 | 2/2 | 32/54 (59%) | 50 | 49,774 | 2,740 | 1/101 |
| B | E2 | 2 | 1/2 | 2.2 | 8/9 | 0/2 | 24/54 (44%) | 56 | 62,160 | 2,760 | 2/113 |
| B | E3 | 2 | 0/2 | 0.0 | 6/13 | 0/1 | 37/54 (69%) | 66 | 71,346 | 3,216 | 1/132 |
| B, shape D | B0 | 2 | 1/2 | 6.7 | 9/9 | 3/6 | 11/54 (20%) | 20 | 17,553 | 1,446 | 0/39 |
| B, shape D | E3 | 2 | 0/2 | 0.0 | 7/13 | 0/4 | 26/54 (48%) | 103 | 124,702 | 4,802 | 4/206 |

Per call: model A 1,364 input / 33 output tokens on average (the lead's calls are the largest, up to about
1,900, because its brief holds all 12 messages); model B 1,037 / 50. Parse failures: A 1 of 1,176 calls
(0.09%), B 4 of 553 (0.7%; 2 of 430 in round 1), B shape D 4 of 245. Every finished episode ended
`quiescent` (no team reached the 200-call cap; individual agents did reach their 40-turn cap, see below).

Observations from the logs (n = 2, for orientation only):

* B0 is now off the floor and, for model B, off the ceiling: A has 4-9 duplicates per 100 required effects
  (1-2 per episode), B 0 and 5 per episode. E1 and E3 have none (the key suppressed 2-6 repeats per episode in A).
* Model A's E2 DAR (51) comes from one worker in seed 1 that looped `lookup` / `send_message` on the same
  ineligible customer 19 times (`runs/pilot/r2/A/F-E2-s1`, agent w5; the same agent looped in round 1).
  It is model behavior, logged and attributed by the metrics; it is a reason to report per-episode
  spreads and medians next to pooled DAR.
* In model A, the P1 and E2 leads never had a done accepted: all four used their 40-turn cap reading the
  board, the inbox and `verify` (one E2 lead done was refused by the gate), and in both P1 seeds worker w2
  also ran out of turns. That is why A's P1 and E2 show PCR 0/0 and the most calls per episode.
* E2/E3 refuse false dones only for effects that did not land; omissions (a missing refund) still pass
  the gate, so FCR stays above 0 under E2/E3, as designed.
* Model B completes much less of the task (44-69%) and over-reports done (FCR 0.46-1.0); its leads
  report done while workers still run (PCR 2/2 in B0, P1, E1; 0 under the E2 gate).

## Frozen settings

`configs/frozen.json` (read by `scripts/run_grid.py`):

| setting | value |
|---|---|
| `p_timeout` (applied but reported as timeout) | **0.1** (pilot change from 0.2) |
| `p_bounce` (send queued, bounces later) | 0.15 |
| `p_stale_board` (claim visible one tick late) | 0.5 |
| `delivery_delay` | 2 ticks |
| batch | 12 messages, 3 customers write twice |
| turn cap / max ticks / lease | 40 per agent / 300 / 6 ticks |
| temperature | 0.7 |
| prompt budget | about 1,200 estimated input tokens per call (system prompt and the brief are always kept; then the agent's own one-line log of earlier calls and messages; full earlier exchanges only while they fit, at most 3) |
| output limit | 256 tokens |
| LLM calls per episode (whole team) | 200 (end reason `call_cap`, a valid ending) |
| failed calls before an episode stops | 1 (a call that still fails after the client's retries stops the episode as `aborted_client_error`; it is rerun later from the cache, so no valid episode contains a provider-caused no-op) |

Code: round 2 ran at commit `9a8c98f` plus the prompt and client changes committed with this file
(the `queued` note, a 1,000-character cut on rendered tool results, a 150 s HTTP timeout, and releasing
the input-token reservation of a 5xx attempt). Round-2 episodes replay byte for byte at that commit
(`python -m dst.run rerun --replay runs/pilot/r2/A/F-B0-s1` and others checked). Round-1 logs were made
with the earlier prompts (`9a8c98f`).

## Wall time and capacity

Measured:

* **Model A**: round 2 took 10:28 AM-1:08 PM (160 min) for 10 episodes and 1,176 calls with 8-10 episodes
  in parallel: **7.4 calls/min**. During the whole pilot the Gemini API answered HTTP 500 (`INTERNAL`) to a
  large share of requests (success per attempt 0.66-0.86 in 10-minute windows from 9:19 to 9:58 AM;
  4 of 4 direct test requests to `gemma-4-31b-it` and 3 of 8 to `gemma-4-26b-a4b-it` failed at 8:50 AM)
  and the median latency per completed call was 11-75 s. The client retries 500s with backoff; an
  episode whose call still fails is stopped and resumed later from the cache.
* The binding free limit for A is input tokens per minute: 80% of 16K = 12.8K/min at 1,364 tokens per call
  is a ceiling of **9.4 calls/min**, reached only when latency is low enough (it was not, most of the day).
* **Model B**: 10 episodes, 553 calls in 11.7 min with 2 in parallel (47 calls/min; median latency 1.8 s),
  **2.0-2.3 neurons per call**, 110-140 neurons per episode.

Projection for the core grid, 5 conditions x 2 shapes (F, D) x 8 seeds = 80 episodes per model, from the
round-2 calls per episode (F: A 118, B 55; D measured on B at 61, i.e. about 1.1 x F, so A's D is taken as
about 130 calls):

| model | calls | input tokens | time | binding limit |
|---|---|---|---|---|
| A | about 9,900 | about 13.5 M | **17.6 h** at the TPM ceiling; **22.5 h** at the pilot's 7.4 calls/min | 16K input tokens/min (RPD: 9,900 of the 12,960 cap, fits) |
| B | about 4,640 | about 5 M | about 1.6 h of calls, but **two neuron days**: 9,300-10,700 neurons against the 9,000 cap per UTC day | 9,000 neurons per UTC day (resets 6:00 PM CST) |

So model A's full core grid does not fit one day under the free limits. Options, cheapest first in lost
design: run A's grid from right after the freeze and across Saturday night (about 18-23 h); or 6 seeds
(60 episodes: 13-17 h); or both shapes on B0, P1 and E3 only (48 episodes: 10.5-13.5 h); or shape F only
with 8 seeds (40 episodes: 8.5-10.7 h). Model B's grid fits in Saturday with one wait: about 85-95% of it
on Saturday's UTC allocation before 6 PM CST, the rest after the reset.

The runner is resumable and stops cleanly at a quota (`--deadline-min` also lets it run in chunks): an
interrupted episode is rerun from the response cache, so no call already made is spent again.
