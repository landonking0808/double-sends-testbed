# Provenance

- All code in this repository was written Nov 6-8 2026 during the Apart Research Swarm Alignment Sprint. There is no preexisting code.
- The core grid runs on tag `v1-frozen` (environment, prompts, fault rates and LLM settings frozen after the pilot; see `configs/frozen.json` and `docs/PILOT.md`).
- On Nov 6 2026 the commit messages were edited once (message text only, file trees unchanged), so commit ids changed. Run manifests written before that edit record the old ids. Mapping for the ids that appear in manifests (tree hashes are identical):
  - `2476c0d` (old) = `9037178` (new): Report pipeline: official Apart template (Google export docx
  - `4002300` (old) = `8c293b0` (new): Log-only monitors (Track 4): report-only and full-log views 
  - `40d01e2` (old) = `8284b93` (new): Analysis pipeline: episode table, Table 1 with seed-bootstra
  - `4703437` (old) = `d8eb23f` (new): Grid loop: rerun the resumable grid until complete, STOP fil
  - `697c816` (old) = `1ddba7f` (new): Tests (env, tasks, metrics, analytic validation, LLM agent w
  - `7f5c2e1` (old) = `3cb6657` (new): Pilot (Nov 6): 2 seeds x 5 conditions x shape F on both mode
  - `9a770cd` (old) = `b6c58ed` (new): Client: a 429 that reports a free daily allocation used up (
  - `9a8c98f` (old) = `1591070` (new): LLM integration: episodes stop (aborted_quota / aborted_clie
  - `de1a7d9` (old) = `5996b6d` (new): Monitor: view budget 5,500 (Gemma counts up to 1.6x the char
  - `f8d1f13` (old) = `53c6d67` (new): LLM client layer: Gemini (Gemma 4) and Workers AI clients wi
- Changes after `v1-frozen` that the grid also ran with: `b6c58ed` (a 429 reporting a used-up free daily allocation stops the episode at once instead of retrying; error handling only, no effect on the content of any completed episode).
