---
id: 129-iceberg-deep
title: Iceberg stage 2 — write/merge doctor, small-files risk, partition spec, v1-v3 matrix
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_iceberg.py tests/unit/test_iceberg_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_databricks_emr.md` makes Iceberg the shared data model for the
platform cycle (LF → EMR → Databricks). Stage 2 deepens the existing
`IcebergProjectModel`: MERGE reconstruction, write-API posture, partition
transforms, format-version feature matrix, static small-file risk.

# Acceptance Criteria

## SqlIndex additions (`analyzers/sql_ast.py`)
- `SqlStatement` gains (defaults, backward-compatible): `merge_source: str`,
  `merge_on_cols: tuple[str, ...]` — from `exp.Merge` (`using`/`on` args).

## Model additions (`analyzers/iceberg_model.py`)
- Write-API evidence (kind `write_api`, value `v2`|`legacy`): `writeTo` →
  v2; `insertInto`/`saveAsTable`/`write.insertInto`/`write.saveAsTable`/
  `df.write.save` on iceberg-evidenced files → legacy.
- Partition spec: `PARTITIONED BY (...)` items parsed to
  `partition.<column>` property evidence with transform value
  (`identity|bucket|truncate|year|month|day|hour`); keep the existing
  `partitioned-by` marker.
- MERGE evidence kind `merge_detail`: value = target; carries source +
  on-cols via name/value fields (name=source, value=on-col list).
- Catalog classification: impl value containing glue/rest/hadoop/hive/nessie
  → `catalog_type` evidence.

## New checks (existing IDs keep; never renumber)
- `ICE020` legacy writer API on iceberg-evidenced files — INFO.
- `ICE021` `insertInto` on a catalog-qualified iceberg table — INFO.
- `ICE022` MERGE/UPDATE/DELETE detected but `spark.sql.extensions` never
  configures `IcebergSparkSessionExtensions` — WARNING.
- `ICE023` MERGE `ON` cols don't intersect known partition columns —
  INFO (partition pruning unlikely).
- `ICE024` `repartition`/`coalesce` call in a file with write evidence —
  INFO, "possible single-partition write pattern" (arg not captured —
  honest heuristic).
- `ICE025` feature vs format-version: `write.delete.mode=merge-on-read`
  (needs v2) or v3 markers (deletion vectors) while format-version=1 —
  WARNING via `knowledge/iceberg/spec.json` matrix.

## CLI
- `forge-doctor iceberg merge [path]` — per-MERGE reconstruction:
  target/source/ON columns/partition-predicate present? — the reviewer's
  example shape.
- `forge-doctor iceberg files [path]` — STATIC RISK small-file posture
  (repartition/coalesce + append evidence), labeled static.

## Packs/docs/tests
- `knowledge/iceberg/spec.json` — format_versions 1/2/3 features
  (row-level deletes v2, deletion-vectors/variant/row-lineage v3) +
  write-mode floors. schema_version 2 + sources.
- checks.md new ICE rows; CHANGELOG; tests for each new check + CLI.

# Constraints
- Merge source uniqueness stays UNKNOWN statically — inspect shows it,
  no check asserts it.
- All new findings deterministic; no new deps.
