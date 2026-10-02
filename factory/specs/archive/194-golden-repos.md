---
id: 194
title: Golden Repositories - Full-Output Snapshot Regression Corpus
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_golden.py tests/unit/adversarial/test_golden.py -x -q
  - python -m forge_doctor golden run
  - python -m forge_doctor golden update && python -m forge_doctor golden run
---

# Roadmap-2 Phase 3 - Golden Repositories

## Context

Labs assert partial truth; goldens pin the *entire* engine output so any
semantic regression diffs a committed snapshot. Corpus of realistic
mini-repos spanning the doc's real-world topology list.

## Acceptance Criteria

- `core/golden.py`:
  - `golden/<name>/repo/` project + `golden/<name>/expected/*.json`
    snapshots: findings (id/severity/file/line/fingerprint), graph
    (entities + relationships), root_causes, remediations, migrations.
  - `snapshot_project(ctx, repo_dir)` — deterministic, sorted.
  - `run_golden`/`run_all_golden` → `GoldenReport` with per-artifact
    add/remove diffs; missing/corrupt snapshots fail honestly.
  - `update_golden` regenerates snapshots (human diff review blesses).
  - Scan scope is `repo/` only — `expected/` must never feed evidence.
- `cli/golden.py`: `golden list|run|update`, `--json`, exit 1 on drift.
- Corpus: `airflow-glue-athena`, `databricks-delta`, `dynamodb-neptune`,
  `dynamodb-streams-lambda`, `emr-iceberg`, `glue-4-to-5`,
  `kafka-spark-iceberg` (with runtime progress artifacts),
  `lf-cross-account`.
- Tests + adversarial (corrupt snapshot, directioned diffs,
  snapshot-text non-contamination, order independence).

## Constraints

- Snapshots are generated, then reviewed — never hand-edited wish lists.
- Deterministic serialization (sorted rows, stable JSON).

## Review Notes

- Bug found by the corpus: `migration.py` iterated dict keys then
  indexed them — `TypeError` on the databricks-delta repo. Fixed
  (`dbr for dbr in reversed(list(rts))`).
- `discover_golden` initially returned the parent dir, letting
  `expected/` JSON contaminate evidence scans — fixed to return the
  `repo/` dir; an adversarial test pins the invariant.
