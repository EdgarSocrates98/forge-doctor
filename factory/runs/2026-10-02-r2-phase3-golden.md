# Roadmap-2 Phase 3 run — Golden Repositories

- Spec: `factory/specs/active/194-golden-repos.md`
- Commit message: `feat(golden): add golden repository snapshot regression corpus`

## Implemented

- `core/golden.py` — `snapshot_project(ctx, repo_dir)` producing five
  deterministic artifacts (findings w/ fingerprints, graph entities +
  relationships, root_causes, remediations, migrations);
  `run_golden`/`run_all_golden` with per-artifact add/remove diffs;
  `update_golden` regenerates snapshots for human-blessed review;
  `discover_golden` returns `golden/<name>/repo` dirs.
- `cli/golden.py` — `golden list|run|update`, `--json`, exit 1 on drift.
- Corpus (8 repos): `airflow-glue-athena` (DAG→Glue→Athena +
  query-stats runtime artifact), `databricks-delta` (job cluster,
  DeltaTable ops, bundle), `dynamodb-neptune` (stream→Lambda→Gremlin
  upsert), `dynamodb-streams-lambda` (ESM + reserved concurrency),
  `emr-iceberg` (EMR 6.15 + Iceberg catalog), `glue-4-to-5` (awsglue
  job + repartition(1)), `kafka-spark-iceberg` (MS Kafka→SS→Iceberg +
  3 progress batches), `lf-cross-account` (settings + resource link +
  grant).

## Bugs caught by the corpus

- `migration.py` `_databricks_upgrade`: `reversed(list(rts))` iterated
  dict keys then `r["dbr"]` indexed strings → `TypeError`. Fixed.
- `discover_golden` returned `repo` parents → `expected/` snapshot JSON
  (which mentions domain tokens like "iceberg") re-entered the evidence
  scan and produced phantom diffs. Fixed to return `repo/` dirs; an
  adversarial test pins non-contamination.

## Snapshot highlights (verified content)

- kafka-spark-iceberg: KFK004 + STREAM080 + `iceberg-v1-to-v2`,
  `streaming-modernize` plans + RC_STREAM_COMMITS chain.
- dynamodb-neptune: NEP041/044/045, NEPCD001, LAM003/004, DDBSTR*.
- glue-4-to-5: `glue-4-to-5`, `parquet-to-delta`, `parquet-to-iceberg`
  plans.

## Verification

- `pytest tests/unit/test_golden.py tests/unit/adversarial/test_golden.py`
  → 12 passed
- `golden update` ×2 → byte-identical snapshots (determinism)
- `golden run` → 8/8 pass
- Full suite: 1233 passed; mypy 146 files clean; ruff/format clean.
