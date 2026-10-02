---
id: 113-iceberg-doctor
title: Iceberg Doctor — IcebergProjectModel, ICE### checks, `iceberg` cmd group, packs
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_iceberg_model.py tests/unit/checks/test_iceberg.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_fase2.md` ("Forge Doctor 0.8 — Iceberg Intelligence") supersedes the
original thin spec 113. Requirements per the review:

- An `IcebergProjectModel` semantic model (V11) absorbing evidence from
  Python AST call sites, SqlIndex (when `[sql]` extra present), spark config
  files, and IaC resources — never per-check regex.
- An `iceberg` command group (`inspect`, `maintenance`, `compatibility`)
  returning model-shaped summaries, offline.
- Knowledge packs `knowledge/iceberg/` (schema_version 2 + sources) covering
  Glue/EMR/Athena ↔ Iceberg runtime compatibility used by compat checks.
- Checks consume the model only. Reviewer's ID list is aspirational; the
  first honest tranche below.

# Acceptance Criteria

## Model (`analyzers/iceberg_model.py`)
- `IcebergEvidence(kind, name, value, file, line, source)` frozen dataclass;
  `kind` ∈ table|catalog|operation|property|maintenance|runtime|extension.
- `IcebergProjectModel` exposes derived views: `tables`, `catalogs`,
  `operations` (merge/append/overwrite/update/delete/insert/read),
  `properties` (table props like `format-version`), `maintenance`
  (expire_snapshots/rewrite_data_files/rewrite_manifests/remove_orphan_files),
  `configs` (`spark.sql.catalog.*`, `spark.sql.extensions`), `runtimes`
  (glue_version pins from IaC), `has_iceberg` bool. Memoized on ctx.
- Evidence sources:
  - **Index call sites** (no re-parse): `.writeTo(...)`, `.using("iceberg")`,
    `write.format("iceberg")`, `.append()/.overwrite()/.createOrReplace()`/
    `createOrReplaceTempView`, `read.table(...)`, `spark.conf.set(...)`
    kwargs/args (catalog + extension keys), `table("...")`.
  - **SqlIndex statements** (only when sqlglot present): `CREATE TABLE ...
    USING iceberg` (+TBLPROPERTIES/`PARTITIONED BY` facts where extractable),
    `MERGE INTO`, `INSERT INTO/OVERWRITE`, `DELETE FROM`, `UPDATE`,
    `ALTER TABLE ... SET TBLPROPERTIES`, and `CALL <cat>.system.<proc>`
    (statement text scan — sqlglot parses CALL loosely). Catalog-prefixed
    table refs (`glue_catalog.db.t`) mark catalog usage.
  - **Config files**: `spark-defaults.conf`/`*.properties` lines matching
    `spark.sql.catalog.*` / `spark.sql.extensions`.
  - **IaC** (`project_iac`): `aws_glue_job`/`AWS::Glue::Job` glue_version →
    runtime evidence; `aws_s3tables_table_bucket`/`aws_glue_catalog_*` →
    catalog evidence.
- Works fully without sqlglot (SQL-derived facts absent); deterministic
  ordering by (file, line, kind, name).

## Checks (`checks/iceberg.py`, category `iceberg`)
- `ICE000` surface anchor (INFO: counts by kind; PASS when no usage).
- `ICE001` format-version risk: `format-version` absent or `1` in table
  properties while MERGE/UPDATE/DELETE ops exist → WARNING.
- `ICE002` MERGE INTO on a table with no partitioning evidence → INFO.
- `ICE008` writes detected + zero `expire_snapshots` maintenance → WARNING.
- `ICE009` writes detected + no `rewrite_data_files` strategy → INFO.
- `ICE010` writes detected + no `rewrite_manifests` strategy → INFO.
- `ICE012` same `spark.sql.catalog.<name>` configured with different impl
  values across sources → WARNING.
- `ICE013` runtime compat: Iceberg usage + a Glue version pin the
  `knowledge/iceberg/compatibility` pack marks risky for the detected ops →
  WARNING (severity from pack); absent pack data → silent (no guessing).
- Every check: why/when_ok/fix, file/line evidence on the finding's source
  evidence entry.
- Register unconditionally (sqlglot optional); document in `explain`.

## Commands (`cli/iceberg.py`, `app.add_typer(iceberg_app, "iceberg")`)
- `forge-doctor iceberg inspect [path]` — Rich summary: runtimes, catalogs,
  operation counts, maintenance detected/not-detected, and the ICE check
  findings summary (like the reviewer's example block).
- `iceberg maintenance` — maintenance posture only (each maintenance op:
  detected at file:line or "not detected").
- `iceberg compatibility` — detected runtimes × pack compat entries.
- No `migrate`/`merge` subcommands yet — follow-up spec.
- Commands work on a bare repo (zero-config V8), no crashes when model is
  empty.

## Knowledge pack
- `knowledge/iceberg/versions.json` + `knowledge/iceberg/compatibility.json`,
  `schema_version: 2`, `sources`/`verified_at` fields; rows only where facts
  are stated in AWS docs (Glue 6.0 → Spark 4.1.1 + Iceberg 1.11.0 is already
  asserted in the glue pack — reuse).

## Docs/tests
- docs/checks.md `## Iceberg` section; README row; CHANGELOG.
- Tests: model facts from each source family; each check positive/negative;
  degradation without sqlglot; deterministic order; `iceberg inspect`/
  `maintenance`/`compatibility` via CliRunner on fixture repos.

# Constraints
- Evidence-gated: no evidence → INFO/absence wording, never false precision.
- Offline only; no AWS calls; never executes analyzed code.
- Deterministic output ordering everywhere.

# Review Notes
- Deliberately deferred (future specs): `iceberg migrate`/`iceberg merge`,
  partition-cardinality heuristics (ICE005), orphan-file risk (ICE019),
  SQL→lineage wiring, Athena/S3-Tables deep packs.
