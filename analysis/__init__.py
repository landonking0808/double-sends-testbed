"""Analysis of testbed episodes: episode tables, pooled metrics with intervals, tests
against the B0 baseline, figures, replay checks, audit digests and a secret scan.

Every number is computed from the episode logs (``ledger.json``, ``scheduler.jsonl``,
``episode.jsonl``) through ``dst.metrics``; nothing is typed in by hand. All random
procedures (bootstrap, permutation tests, sampling) take a fixed seed, so the same
inputs give byte-identical outputs.
"""
