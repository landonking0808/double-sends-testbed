# Grid results summary (runs/grid/A, runs/grid/B)

Generated from results/grid/table1.csv, tests.csv and episodes.csv (scripts/analyze.py, defaults: seed-cluster bootstrap 5,000 draws seed 0; permutation 10,000 draws seed 0). Fault profile t0.1-b0.15-s0.5-d2 for every episode. Model A = Gemma 4 gemma-4-26b-a4b-it (Gemini API); model B = IBM Granite 4.0 micro (@cf/ibm-granite/granite-4.0-h-micro, Workers AI).

## Episodes

- 160 episode directories found, 160 included, 0 excluded. Each model has exactly 80 episodes, one per cell (shapes F, D x conditions B0, P1, E1, E2, E3 x seeds 1-8); no cell missing, no duplicate, none aborted. Every episode's final state in grid_manifest.json is `done` with end reason `quiescent` or `call_cap`, and every per-episode number in the manifests (DAR extras/required, false/premature dones, task success, calls, input tokens) matches episodes.csv.
- LLM call cap (200 calls): 20 episodes, all shape D: model A 19 (D-B0 2, D-P1 6, D-E1 2, D-E2 4, D-E3 5), model B 1 (D-E2-s2). Tick cap (300 ticks): 0 episodes (max ticks used: 85). Capped episodes are kept and counted as outcomes; no capped episode has an accepted lead done report.

## Table: per model x shape x condition (n = 8 seeds per shape, 16 for `all`)

DAR = extra effective side effects per 100 required effects, pooled, seed-bootstrap 95% CI. FCR (all roles) = false done reports / accepted done reports; lead FCR is the lead's share; PCR = premature done reports / done reports by nodes with children; task success = requirements met / requirements. Shares in %, 95% Wilson CI, (k/n).

| model | shape | cond | DAR/100 [95% CI] | FCR all | lead FCR | PCR | task success | mean LLM calls | mean tokens (in / out) | token overhead vs B0 |
|---|---|---|---|---|---|---|---|---|---|---|
| A gemma-4-26b-a4b-it | F | B0 | 46.0 [25.7, 67.6] | 66 [51, 78] (27/41) | 100 [65, 100] (7/7) | 29 [8, 64] (2/7) | 88 [83, 92] (190/216) | 121.4 | 169,486 / 4,598 | baseline |
| A gemma-4-26b-a4b-it | F | P1 | 21.3 [13.0, 31.4] | 48 [28, 68] (10/21) | n/a | n/a | 84 [78, 88] (181/216) | 149.2 | 225,642 / 4,056 | +32% |
| A gemma-4-26b-a4b-it | F | E1 | 0.0 [0.0, 0.0] | 74 [57, 85] (25/34) | 100 [57, 100] (5/5) | 0 [0, 43] (0/5) | 82 [77, 87] (178/216) | 117.4 | 168,735 / 4,230 | -1% |
| A gemma-4-26b-a4b-it | F | E2 | 54.0 [35.8, 74.7] | 32 [19, 50] (10/31) | 33 [6, 79] (1/3) | 0 [0, 56] (0/3) | 94 [91, 97] (204/216) | 137.2 | 198,978 / 4,403 | +17% |
| A gemma-4-26b-a4b-it | F | E3 | 0.0 [0.0, 0.0] | 46 [30, 64] (13/28) | 100 [44, 100] (3/3) | 0 [0, 56] (0/3) | 90 [85, 93] (194/216) | 124.6 | 185,806 / 4,054 | +9% |
| A gemma-4-26b-a4b-it | D | B0 | 60.9 [37.9, 87.9] | 38 [27, 50] (24/64) | 100 [51, 100] (4/4) | 6 [1, 26] (1/18) | 94 [91, 97] (204/216) | 167.1 | 227,456 / 6,033 | baseline |
| A gemma-4-26b-a4b-it | D | P1 | 16.7 [6.4, 26.9] | 33 [20, 50] (12/36) | n/a | 0 [0, 49] (0/4) | 90 [85, 93] (194/216) | 195.5 | 281,494 / 5,381 | +23% |
| A gemma-4-26b-a4b-it | D | E1 | 0.0 [0.0, 0.0] | 52 [39, 65] (26/50) | 100 [44, 100] (3/3) | 25 [9, 53] (3/12) | 88 [83, 92] (191/216) | 181.1 | 252,376 / 5,584 | +10% |
| A gemma-4-26b-a4b-it | D | E2 | 62.6 [45.2, 82.9] | 12 [5, 23] (6/52) | 0 [0, 66] (0/2) | 0 [0, 28] (0/10) | 96 [93, 98] (208/216) | 190.2 | 265,120 / 6,342 | +16% |
| A gemma-4-26b-a4b-it | D | E3 | 0.0 [0.0, 0.0] | 37 [25, 51] (17/46) | 100 [21, 100] (1/1) | 0 [0, 32] (0/8) | 90 [85, 93] (194/216) | 190.1 | 271,754 / 5,901 | +19% |
| A gemma-4-26b-a4b-it | all | B0 | 53.4 [34.5, 74.0] | 49 [39, 58] (51/105) | 100 [74, 100] (11/11) | 12 [4, 30] (3/25) | 91 [88, 94] (394/432) | 144.2 | 198,471 / 5,316 | baseline |
| A gemma-4-26b-a4b-it | all | P1 | 19.0 [11.1, 27.6] | 39 [27, 52] (22/57) | n/a | 0 [0, 49] (0/4) | 87 [83, 90] (375/432) | 172.4 | 253,568 / 4,718 | +27% |
| A gemma-4-26b-a4b-it | all | E1 | 0.0 [0.0, 0.0] | 61 [50, 70] (51/84) | 100 [68, 100] (8/8) | 18 [6, 41] (3/17) | 85 [82, 88] (369/432) | 149.2 | 210,556 / 4,907 | +6% |
| A gemma-4-26b-a4b-it | all | E2 | 58.3 [41.5, 76.4] | 19 [12, 29] (16/83) | 20 [4, 62] (1/5) | 0 [0, 23] (0/13) | 95 [93, 97] (412/432) | 163.8 | 232,049 / 5,373 | +17% |
| A gemma-4-26b-a4b-it | all | E3 | 0.0 [0.0, 0.0] | 41 [30, 52] (30/74) | 100 [51, 100] (4/4) | 0 [0, 26] (0/11) | 90 [87, 92] (388/432) | 157.4 | 228,780 / 4,978 | +15% |
| B granite-4.0-h-micro | F | B0 | 12.1 [6.2, 18.5] | 83 [71, 91] (44/53) | 100 [68, 100] (8/8) | 88 [53, 98] (7/8) | 47 [41, 54] (102/216) | 41.8 | 40,045 / 2,485 | baseline |
| B granite-4.0-h-micro | F | P1 | 6.3 [2.9, 9.9] | 82 [70, 90] (46/56) | 100 [68, 100] (8/8) | 100 [68, 100] (8/8) | 51 [44, 58] (110/216) | 53.6 | 54,493 / 2,682 | +34% |
| B granite-4.0-h-micro | F | E1 | 0.0 [0.0, 0.0] | 96 [87, 99] (50/52) | 100 [68, 100] (8/8) | 100 [68, 100] (8/8) | 52 [46, 59] (113/216) | 45.8 | 45,055 / 2,641 | +12% |
| B granite-4.0-h-micro | F | E2 | 10.3 [4.0, 17.9] | 90 [78, 96] (38/42) | 100 [57, 100] (5/5) | 0 [0, 43] (0/5) | 46 [40, 53] (100/216) | 89.4 | 108,790 / 4,880 | +167% |
| B granite-4.0-h-micro | F | E3 | 0.0 [0.0, 0.0] | 77 [64, 86] (40/52) | 100 [61, 100] (6/6) | 0 [0, 39] (0/6) | 62 [55, 68] (133/216) | 81.2 | 95,158 / 4,423 | +134% |
| B granite-4.0-h-micro | D | B0 | 16.1 [5.8, 27.2] | 95 [87, 98] (62/65) | 100 [68, 100] (8/8) | 88 [69, 96] (21/24) | 40 [34, 47] (87/216) | 49.4 | 48,369 / 2,885 | baseline |
| B granite-4.0-h-micro | D | P1 | 14.9 [8.7, 21.7] | 81 [70, 88] (55/68) | 100 [68, 100] (8/8) | 100 [86, 100] (24/24) | 45 [38, 52] (97/216) | 61.9 | 63,599 / 3,183 | +30% |
| B granite-4.0-h-micro | D | E1 | 0.0 [0.0, 0.0] | 75 [64, 84] (52/69) | 100 [68, 100] (8/8) | 100 [86, 100] (24/24) | 59 [53, 66] (128/216) | 53.9 | 53,332 / 3,042 | +10% |
| B granite-4.0-h-micro | D | E2 | 15.5 [6.9, 25.4] | 80 [67, 88] (43/54) | 100 [57, 100] (5/5) | 0 [0, 17] (0/19) | 43 [37, 50] (93/216) | 106.1 | 125,062 / 4,961 | +154% |
| B granite-4.0-h-micro | D | E3 | 0.0 [0.0, 0.0] | 77 [66, 86] (48/62) | 100 [57, 100] (5/5) | 0 [0, 17] (0/19) | 52 [45, 58] (112/216) | 110.9 | 133,653 / 5,585 | +172% |
| B granite-4.0-h-micro | all | B0 | 14.1 [8.0, 21.1] | 90 [83, 94] (106/118) | 100 [81, 100] (16/16) | 88 [72, 95] (28/32) | 44 [39, 48] (189/432) | 45.6 | 44,207 / 2,685 | baseline |
| B granite-4.0-h-micro | all | P1 | 10.6 [6.4, 15.0] | 81 [74, 87] (101/124) | 100 [81, 100] (16/16) | 100 [89, 100] (32/32) | 48 [43, 53] (207/432) | 57.8 | 59,046 / 2,933 | +32% |
| B granite-4.0-h-micro | all | E1 | 0.0 [0.0, 0.0] | 84 [77, 90] (102/121) | 100 [81, 100] (16/16) | 100 [89, 100] (32/32) | 56 [51, 60] (241/432) | 49.8 | 49,194 / 2,841 | +11% |
| B granite-4.0-h-micro | all | E2 | 12.9 [8.0, 18.4] | 84 [76, 90] (81/96) | 100 [72, 100] (10/10) | 0 [0, 14] (0/24) | 45 [40, 49] (193/432) | 97.8 | 116,926 / 4,921 | +160% |
| B granite-4.0-h-micro | all | E3 | 0.0 [0.0, 0.0] | 77 [69, 84] (88/114) | 100 [74, 100] (11/11) | 0 [0, 13] (0/25) | 57 [52, 61] (245/432) | 96.1 | 114,405 / 5,004 | +155% |

## B0 vs each control (Holm-adjusted)

Fisher exact tests on episode-level outcomes (any duplicate, any false done, any premature done, task incomplete) and a permutation test on per-episode DAR (stratified by shape for `all`). Holm adjustment within each family of 4 controls per model x shape x outcome (no adjustment across outcomes, shapes or models). Cells show the B0 -> control value and the Holm p; **bold** = Holm p < 0.05. DAR values here are per-episode means, so they differ slightly from the pooled DAR above.

| model | shape | control | DAR/100 (mean) | eps with dup | eps with false done | eps with premature | eps task incomplete |
|---|---|---|---|---|---|---|---|
| A gemma-4-26b-a4b-it | F | P1 | 46.7 -> 21.5, p=0.13 | 100% -> 100%, p=1 | 100% -> 88%, p=1 | 25% -> 0%, p=1 | 100% -> 100%, p=1 |
| A gemma-4-26b-a4b-it | F | E1 | **46.7 -> 0.0, p=0.0008** | **100% -> 0%, p=0.00062** | 100% -> 88%, p=1 | 25% -> 0%, p=1 | 100% -> 100%, p=1 |
| A gemma-4-26b-a4b-it | F | E2 | 46.7 -> 54.0, p=0.64 | 100% -> 100%, p=1 | 100% -> 62%, p=0.8 | 25% -> 0%, p=1 | 100% -> 62%, p=0.8 |
| A gemma-4-26b-a4b-it | F | E3 | **46.7 -> 0.0, p=0.0008** | **100% -> 0%, p=0.00062** | 100% -> 100%, p=1 | 25% -> 0%, p=1 | 100% -> 100%, p=1 |
| A gemma-4-26b-a4b-it | D | P1 | **61.9 -> 16.5, p=0.011** | 100% -> 62%, p=0.4 | 88% -> 75%, p=1 | 12% -> 0%, p=1 | 88% -> 88%, p=1 |
| A gemma-4-26b-a4b-it | D | E1 | **61.9 -> 0.0, p=0.0009** | **100% -> 0%, p=0.00062** | 88% -> 100%, p=1 | 12% -> 38%, p=1 | 88% -> 100%, p=1 |
| A gemma-4-26b-a4b-it | D | E2 | 61.9 -> 62.6, p=0.96 | 100% -> 100%, p=1 | 88% -> 38%, p=0.48 | 12% -> 0%, p=1 | 88% -> 38%, p=0.48 |
| A gemma-4-26b-a4b-it | D | E3 | **61.9 -> 0.0, p=0.0008** | **100% -> 0%, p=0.00062** | 88% -> 88%, p=1 | 12% -> 0%, p=1 | 88% -> 88%, p=1 |
| A gemma-4-26b-a4b-it | all | P1 | **54.3 -> 19.0, p=0.0004** | 100% -> 81%, p=0.45 | 94% -> 81%, p=1 | 19% -> 0%, p=0.9 | 94% -> 94%, p=1 |
| A gemma-4-26b-a4b-it | all | E1 | **54.3 -> 0.0, p=0.0004** | **100% -> 0%, p=1.3e-08** | 94% -> 94%, p=1 | 19% -> 19%, p=1 | 94% -> 100%, p=1 |
| A gemma-4-26b-a4b-it | all | E2 | 54.3 -> 58.3, p=0.73 | 100% -> 100%, p=1 | 94% -> 50%, p=0.062 | 19% -> 0%, p=0.9 | 94% -> 50%, p=0.062 |
| A gemma-4-26b-a4b-it | all | E3 | **54.3 -> 0.0, p=0.0004** | **100% -> 0%, p=1.3e-08** | 94% -> 94%, p=1 | 19% -> 0%, p=0.9 | 94% -> 94%, p=1 |
| B granite-4.0-h-micro | F | P1 | 12.2 -> 6.3, p=0.34 | 88% -> 75%, p=1 | 100% -> 100%, p=1 | 88% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | F | E1 | **12.2 -> 0.0, p=0.0051** | **88% -> 0%, p=0.0056** | 100% -> 100%, p=1 | 88% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | F | E2 | 12.2 -> 10.4, p=0.75 | 88% -> 75%, p=1 | 100% -> 100%, p=1 | **88% -> 0%, p=0.0056** | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | F | E3 | **12.2 -> 0.0, p=0.0044** | **88% -> 0%, p=0.0056** | 100% -> 100%, p=1 | **88% -> 0%, p=0.0056** | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | D | P1 | 16.0 -> 14.9, p=1 | 62% -> 88%, p=1 | 100% -> 100%, p=1 | 100% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | D | E1 | 16.0 -> 0.0, p=0.1 | 62% -> 0%, p=0.1 | 100% -> 100%, p=1 | 100% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | D | E2 | 16.0 -> 15.4, p=1 | 62% -> 75%, p=1 | 100% -> 100%, p=1 | **100% -> 0%, p=0.00062** | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | D | E3 | 16.0 -> 0.0, p=0.1 | 62% -> 0%, p=0.1 | 100% -> 100%, p=1 | **100% -> 0%, p=0.00062** | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | all | P1 | 14.1 -> 10.6, p=0.75 | 75% -> 81%, p=1 | 100% -> 100%, p=1 | 94% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | all | E1 | **14.1 -> 0.0, p=0.0004** | **75% -> 0%, p=6.4e-05** | 100% -> 100%, p=1 | 94% -> 100%, p=1 | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | all | E2 | 14.1 -> 12.9, p=0.8 | 75% -> 75%, p=1 | 100% -> 100%, p=1 | **94% -> 0%, p=2.3e-07** | 100% -> 100%, p=1 |
| B granite-4.0-h-micro | all | E3 | **14.1 -> 0.0, p=0.0004** | **75% -> 0%, p=6.4e-05** | 100% -> 100%, p=1 | **94% -> 0%, p=2.3e-07** | 100% -> 100%, p=1 |

## Cost per episode (shapes pooled, means)

| model | cond | LLM calls | tokens | overhead vs B0 |
|---|---|---|---|---|
| A gemma-4-26b-a4b-it | B0 | 144.2 | 203,787 | baseline |
| A gemma-4-26b-a4b-it | P1 | 172.4 | 258,286 | +27% |
| A gemma-4-26b-a4b-it | E1 | 149.2 | 215,462 | +6% |
| A gemma-4-26b-a4b-it | E2 | 163.8 | 237,422 | +17% |
| A gemma-4-26b-a4b-it | E3 | 157.4 | 233,758 | +15% |
| B granite-4.0-h-micro | B0 | 45.6 | 46,892 | baseline |
| B granite-4.0-h-micro | P1 | 57.8 | 61,978 | +32% |
| B granite-4.0-h-micro | E1 | 49.8 | 52,035 | +11% |
| B granite-4.0-h-micro | E2 | 97.8 | 121,847 | +160% |
| B granite-4.0-h-micro | E3 | 96.1 | 119,409 | +155% |

Grid totals: model A 12,592 LLM calls, 17,974,777 input and 404,656 output tokens; model B 5,551 calls, 6,140,450 input and 294,140 output tokens.

## Key findings (read off the tables above)

1. The idempotency controls (E1, E3) removed duplicate side effects completely: DAR was 0.0 in every E1 and E3 cell for both models, against a B0 DAR of 53.4 [34.5, 74.0] per 100 required effects for Gemma and 14.1 [8.0, 21.1] for Granite (shapes pooled; Holm p = 0.0004 for both models and both controls). Per shape this is significant in all cells except Granite shape D (Holm p = 0.1, B0 had duplicates in 5/8 episodes).
2. The prompt-only control (P1) lowered Gemma's DAR from 53.4 to 19.0 [11.1, 27.6] (Holm p = 0.0004 pooled, 0.011 in shape D, 0.13 in shape F), but 13/16 Gemma P1 episodes still had a duplicate; for Granite P1 did not change DAR significantly (14.1 -> 10.6, Holm p = 0.75).
3. The completion gate alone (E2) did not reduce duplicates for either model (Gemma 58.3, Granite 12.9 per 100). It removed premature done reports for Granite (episodes with a premature done 94% -> 0%, Holm p = 2.3e-07; E3 the same), and for Gemma it cut the false-done rate over accepted reports from 49% [39, 58] to 19% [12, 29], with episodes containing a false done going 94% -> 50% (Holm p = 0.062, not significant at 0.05).
4. False dones stayed frequent under every control: FCR over accepted done reports was 77-90% for Granite in every condition and 19-61% for Gemma; Granite's lead had a false done in every accepted lead done report in every condition (lead FCR 100%, 69/69 pooled across conditions).
5. Gemma met far more task requirements (85-95% across conditions) than Granite (44-57%), but whole-task completion was rare: Granite completed no episode (0/80) and Gemma completed 11/80, 8 of them under E2.
6. Cost: Gemma used about 4x the tokens of Granite per episode at B0 (203,787 vs 46,892) and hit the 200-call cap in 19 of its 40 shape-D episodes. The controls cost Gemma +6% to +27% tokens; for Granite E1 cost +11% and P1 +32%, while E2 and E3 cost +160% and +155%, alongside 29.6 and 25.1 refused reports per episode.

## Notes and caveats

- E1/E3's zero DAR is enforced by the environment (it suppresses the repeated effect: 4.4 and 3.5 suppressed effects per Granite episode, 6.8 and 8.4 per Gemma episode), not by the agents avoiding the repeat.
- Lead FCR and PCR rest on few reports for Gemma: its lead filed an accepted done report in 28 of 80 episodes and in none of the 16 P1 episodes (lead FCR n/a there), and no call-capped episode has a lead done report.
- Statistical tests are episode-level (any duplicate / any false done / any premature / task incomplete) plus a permutation test on per-episode DAR; there is no test on the report-level FCR or the requirement-level task success rates.
- Episodes were produced by three code commits (34690be5a6d9 for 126 episodes; 4703437a3190 and 2476c0d8ce84, pre-history-edit ids, for the rest); the only difference to 34690be under dst/ or configs/ is 12 added lines in dst/llm/base.py (quota error handling). 100 episodes reused some responses from the response cache (8,153 of 12,592 Gemma calls and 1,376 of 5,551 Granite calls were cache hits), consistent with the abort-and-rerun procedure.
- Replay check: all 160 grid episodes replayed byte-identical from their recorded responses (episode.jsonl, scheduler.jsonl, ledger.json; prompt hashes checked on every call).
