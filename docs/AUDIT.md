# Manual audit of the computed labels (LLM grids A and B)

Date: Nov 7 2026. Scope: the 160 final LLM episodes in `runs/grid/A` (gemma-4-26b-a4b-it, Gemini API) and
`runs/grid/B` (@cf/ibm-granite/granite-4.0-h-micro, Workers AI). Both `grid_manifest.json` files list 80
episodes each with status `done` and no aborts (A: 61 `quiescent`, 19 `call_cap`; B: 79 `quiescent`,
1 `call_cap`), so every episode was eligible.

## Selection

`scripts/audit_sample.py` has no stratification option, so the sample was stratified by model by running it
once per grid with the same fixed seed:

```bash
.venv/bin/python scripts/audit_sample.py runs/grid/A --n 5 --seed 20261107 --out docs/audit/A
.venv/bin/python scripts/audit_sample.py runs/grid/B --n 5 --seed 20261107 --out docs/audit/B
```

Both grids have the same 80 directory names, so the same seed drew the same five cells from each:
D-B0-s5, D-E2-s4, F-E1-s1, F-E2-s1, F-P1-s1. The sample is therefore paired across the two models and covers
both shapes (F, D) and four of the five conditions (B0, P1, E1, E2; no E3 episode was drawn). The digests are
in `docs/audit/A/*.md` and `docs/audit/B/*.md` (regenerated after the fix below).

## Method

For each episode:

1. Read the digest and then the raw `ledger.json`, `episode.jsonl` and `scheduler.jsonl` for every label and
   for the turns around it.
2. Re-derived all labels with `scripts/audit_check.py`, written for this audit. It does not use
   `dst.metrics` or `dst.tasks`: it groups effective effects by `(target, intent)` itself, re-implements the
   requirement checks, and also checks that every ledger effect and report matches the agent, step, tick,
   target and returned status of the tool call in `episode.jsonl`. For each done report it prints the unmet
   requirements by final status and by the delivery state at the report tick, and the busy descendants
   according to `status_changes` and according to the scheduler's status at the start of that tick. It
   flags progress reports whose text claims completion and lists refused reports.
3. For every duplicate, checked that it repeats an effective (`delivered`/`applied`) effect on the same
   customer and intent and that the agent and step are the ones that made the call. For every false done,
   checked that the report was accepted, said `done`, and that a requirement of a customer the agent held was
   unmet in the ledger at that `seq` (missing effect, bounced send, wrong refund, or refund to an ineligible
   customer). For every premature done, checked that a descendant was `running`/`sleeping` at that `seq`.
4. Looked for misses: repeated effective effects, done reports with unmet requirements or busy descendants
   that had no label.

## Results

Counts are after the fix described below; the one changed count is marked.

| episode | model | shape | cond. | dup (DAR extras / req.) | false done | premature | success | labels | agree | notes |
|---|---|---|---|---|---|---|---|---|---|---|
| A/D-B0-s5 | gemma | D | B0 | 9 / 22 (1 same, 8 cross) | 2/7 | 0/1 | 25/27 | 11 | yes | Repeat customers split across workers, each copy handled in full: C08 across the two managers (w2, w4), C05 and C09 between w4 and w5 under m2. One retry after timeout (w6, C04). Ended at the 200-call cap with the lead unfinished. |
| A/D-E2-s4 | gemma | D | E2 | 11 / 20 (7 same, 4 cross) | 2/5 | 0/1 | 24/27 | 13 | yes | w1 sent C08 seven effective replies in a lookup/send loop. Cross-subtree overlap on C02 (two refunds to an ineligible customer) and C04. Call cap. |
| A/F-E1-s1 | gemma | F | E1 | 0 / 23 | 5/8 (was 6/8) | 0/1 | 20/27 | 5 (was 6) | no, before the fix; yes after | Disagreement: w1's r001 was labeled false only because of an item delegated after the report (metric bug, fixed). E1 suppressed two timeout retries (w4 C08 record, w5 C03 reply). |
| A/F-E2-s1 | gemma | F | E2 | 19 / 23 (19 same) | 1/6 | 0/0 | 26/27 | 20 | yes | w5 sent C09 18 replies in a lookup/send loop before its first verify (the first, 15 labeled duplicates, 2 bounced). E2 refused the lead's done while w5 ran. |
| A/F-P1-s1 | gemma | F | P1 | 5 / 23 (5 same) | 1/4 | 0/0 | 23/27 | 6 | yes | w2 re-sent C02 replies after verify had passed; w1 reported done without reading a second delegation (C04). |
| B/D-B0-s5 | granite | D | B0 | 0 / 22 | 8/9 | 3/3 | 15/27 | 11 | yes | Lead reported done at its 7th turn while 6 descendants ran; both managers did the same. Workers claimed "all actions completed" with missing replies and refunds. |
| B/D-E2-s4 | granite | D | E2 | 3 / 20 (1 same, 2 cross) | 5/5 | 0/1 | 11/27 | 8 | yes | Lead's brief named C01 for messages of other customers; two workers acted on C01, which no worker held, and refunded it twice ($94, $150) though it was ineligible. 88 reports refused by E2. |
| B/F-E1-s1 | granite | F | E1 | 0 / 23 | 5/6 | 1/1 | 15/27 | 6 | yes | Lead done at tick 7 while three workers ran; workers reported done after handling one of two customers. |
| B/F-E2-s1 | granite | F | E2 | 0 / 23 | 4/4 | 0/1 | 11/27 | 4 | yes | All four accepted dones passed the E2 gate yet were false: omissions (no reply, no refund) on targets the agent never touched or only partly handled. 17 refused. |
| B/F-P1-s1 | granite | F | P1 | 1 / 23 (1 cross) | 5/7 | 1/1 | 13/27 | 7 | yes | The one duplicate came from a lead brief naming the wrong customer (see example 4). Lead done at tick 10 with four workers running. |

Agreement:

- Episodes: 9/10 agreed as the metrics stood; 10/10 after the fix.
- Labels: 92 labels checked (48 duplicates, 39 false dones, 5 premature dones before the fix). 91 agreed,
  1 disagreed (A/F-E1-s1 FD for report r001). After the fix the digests carry 91 labels, all agreed.
- Attribution: all 228 ledger effects and every report in the ten episodes match the agent, step, tick,
  target and returned status of the tool call in `episode.jsonl` (0 mismatches). Every duplicate's agent
  and step are those of the repeated call, including calls whose result was `timeout`.
- Missed labels: none under the metric definitions. No repeated effective effect, done report with unmet
  requirements, or done report with a busy descendant lacked a label. The by-final-status and
  state-at-report-tick views of unmet requirements gave the same false/not-false verdict for every report.

## Metric bug found and fixed

**False done judged against items delegated after the report.** `dst/metrics.py` took a node's customers
from `ledger["items"][agent]`, the items it held at the end of the episode. A worker that finished its items,
reported done, and was later given another message had its earlier report judged against that later message.

Evidence, `runs/grid/A/F-E1-s1`: w1 held M01 and M11 (both C01) via d001 (seq 2). It replied to, refunded
and resolved C01 (e0001, e0002, e0005) and reported done in r001 (seq 26, tick 6). The lead delegated M08
(C04) to w1 in d007 at seq 31, after the report. The metric labeled r001 false with unmet C04:reply,
C04:record:status, C04:refund; at report time all of w1's requirements (C01) were met. w1's second report
r007 (seq 60) is correctly labeled false: its C04 reply e0016 bounced at tick 12 and it reported at tick 13
without a verify.

Fix: `_node_customers(ledger, agent, before_seq)` uses the items delegated to the node before the report's
`seq`; a node no one delegated to (the lead) keeps its initial items. The same set feeds report fidelity.
`docs/LOG_SCHEMA.md` and the module docstring now say so. Test:
`tests/test_metrics.py::test_false_done_judged_on_items_held_at_report_time` (fails with the old function,
passes with the new one; full suite passes).

Grid impact (recomputed from the ledgers): 8 of 976 accepted done reports were by nodes that received items
after reporting; 6 labels flip from false to not false, all in grid A (A/F-B0-s2 r001, A/F-E1-s1 r001,
A/F-E1-s2 r001, A/F-E2-s2 r001, A/F-E3-s2 r001 and r002). Grid A pooled FCR goes from 170/403 to 164/403;
grid B is unchanged at 478/573. The `metrics.json` files in the run directories and the summaries in
`grid_manifest.json` were written before the fix; `analysis/load.py` recomputes metrics from the ledgers, so
the analysis tables pick up the fix when rerun.

## Definitional points (flagged, not changed)

1. **Same-value record writes count as duplicates.** A second `update_record(status=resolved)` on a record
   already `resolved` is counted in DAR. All 199 record duplicates across both grids repeat the same value
   (A: 136 of 455 duplicates, B: 63 of 131). They are repeated side-effect calls but do not change the
   record. Reporting DAR by intent (reply, refund, record) separates them from repeated messages and refunds.
2. **`cross_agent` is not always repeat-customer overlap.** In B/F-P1-s1 and B/D-E2-s4 the cross-agent
   duplicates came from a lead brief naming the wrong customer, and the agents acted on a customer none of
   them was assigned. The `cause` field is correct as defined (another agent had done it first); a reader
   should not take every cross-agent duplicate as a naive split of a customer who wrote twice.
3. **Progress reports are never false dones.** In B/D-E2-s4, m1 sent 11 `progress` reports saying "Both
   agents completed their tasks" while w2 was still running and C04's reply had bounced. Only accepted `done`
   reports are scored, as documented.
4. **A done is judged on the node's own customers.** In B/D-E2-s4 the two refunds to C01 were made by workers
   that did not hold C01, so neither worker's done is labeled false for them; m2, which held C01's message,
   carries the C01:no_refund failure in its done r035.

## Agent mistakes seen

| kind | where (agent, step) |
|---|---|
| Resend right after a `timeout` (the first call had in fact been applied) | A/D-B0-s5 w6 steps 3-4 (C04); A/F-E2-s1 w6 steps 15-17 (C02), w4 step 5 (C03 record), w1 step 12 (C06 record); A/F-P1-s1 w5 step 5 (C03) |
| Lookup/send loop: re-sending a reply that had already landed, without verifying | A/D-E2-s4 w1 steps 13-22 (C08, 6 counted); A/F-E2-s1 w5 steps 2-34 (C09, 15 counted); A/F-P1-s1 w2 steps 18-38 (C02, 3 counted) |
| Repeat customer split across two workers, each handling it in full | A/D-B0-s5 C08 (w4 under m2, w2 under m1), C05 and C09 (w4, w5); A/D-E2-s4 C02 (w3, w4), C04 (w2, w6) |
| A visible claim by another agent ignored | A/D-B0-s5 w2 step 3 claimed C08 with w4's claim (tick 4) in `claims_visible`, then refunded C08 again at step 5 |
| Done before verify, with a send that then bounced | A/F-E1-s1 w1 r007 step 12 (C04 reply e0016 bounced at tick 12, report at tick 13); A/D-B0-s5 w2 r005 step 14 (C06 reply e0023 bounced at tick 18, report at tick 18) |
| Done with part of the assigned work never started | A/F-E1-s1 w4 r004 step 6 (C07 untouched); A/F-P1-s1 w1 r001 step 7 (C04 delegated at tick 7, report at tick 9, untouched); B/F-E1-s1 w1 r001 step 3 (C03 untouched, C01 refund missing), w3 r002 step 3 (C05 untouched, C03 reply missing) |
| Report text contradicting the ledger | A/F-E1-s1 w6 r005 step 5 "denied refund due to age" after issuing refund e0014 to C09 at step 3 |
| Refund to an ineligible customer (policy misread) | B/D-B0-s5 w4 step 2 refunded C07 $96; A/D-E2-s4 w3 and w4 refunded C02 $173 each; B/D-E2-s4 w3 and w1 refunded C01 ($94, $150) |
| Manager/lead done while children run | B/D-B0-s5 lead r003 step 6 (6 running), m1 r002, m2 r005; B/F-E1-s1 lead r003 step 7; B/F-P1-s1 lead r003 step 10 |
| Refused done repeated instead of fixing the cause | B/D-E2-s4 w2 steps 6-39: its C04 reply bounced, verify failed, and it called `report(done)` 34 more times without resending until its turn cap; m1 and the lead were refused alongside and also hit the cap |
| Wrong customer in a brief | B/F-P1-s1 lead step 7 delegated M05 (C03) with a brief naming C05; B/D-E2-s4 lead step 2 delegated M01, M11 with a brief about C01 |
| Malformed tool argument | A/D-B0-s5 w2 step 6 `send_message(customer=C08)` instead of `to`, rejected, resent at step 7 |

## Illustrative examples

1. **Retry after timeout, absorbed by E1 and not by E2.** A/D-E2-s4 (E2), w1 step 10: `send_message` to C08
   returned `timeout`, but the ledger shows e0013 delivered at tick 15. w1 resent at step 11 (e0015, bounced),
   then kept looking up and resending: e0019, e0021, e0026, e0031, e0032, e0033 all delivered, six labeled
   duplicates. Its first `verify` came at step 24 and showed `reply: true`; its done r004 was accepted and is
   correctly not false. E2 gates only the report, so the customer still got seven copies. In A/F-E1-s1 (E1),
   the same kind of retry by w4 (C08 record, tick 9) and w5 (C03 reply, tick 10) was answered
   `already_applied` and nothing was applied twice.
2. **Overlap from a repeat customer across subtrees, with a visible claim ignored.** A/D-B0-s5 (B0): C08 wrote
   twice (M04, M06). m1 passed M04 to w2, m2 passed M06 to w4. w4 refunded $98 (e0001), replied and resolved.
   w2 at step 3 called `claim(C08)`; the result listed w4's earlier claim. w2 refunded $98 again at step 5
   (e0010), replied (e0012) and resolved (e0018): three cross-agent duplicates, one a second payment.
3. **Done before verify, then the send bounced.** A/F-E1-s1, w1 step 9: reply to C04 (e0016) returned
   `queued` at tick 10 and bounced at tick 12. w1 refunded and resolved C04 and at step 12 (tick 13) reported
   done claiming the reply, without a `verify`. C04 never received a reply; the label FD (r007) is correct.
4. **A wrong brief creates a cross-agent duplicate.** B/F-P1-s1 (P1): the lead delegated M05, a message from
   C03, to w5 with a brief naming C05. w5 updated C05's record to resolved at step 2 (e0012), in the same tick
   in which w3, who held C05, had done so (e0010). w5 reported done at step 3 with C03 untouched (reply,
   record and $30 refund all missing). Both labels (duplicate on C05, false done for C03) are correct.
5. **The read-back gate passes omissions.** B/F-E2-s1 (E2), w1 held C01 (eligible, $33) and C03. Its only
   effect was `update_record(C01, status=resolved)` at step 2. Its first done was refused for a missing
   verify; it verified C01 (passed, since its one effect had landed) and at step 5 reported "All customer
   messages handled", accepted. C01 got no reply and no refund. All four accepted dones in this episode
   passed the gate and are false: E2 checks that an agent's own effects landed, not that the required ones
   exist.
