# Run: 2026-09-30 — Iceberg Doctor (spec 113, "0.8 Iceberg Intelligence")

Driver: `prompt_fase2.md` — reviewer greenlit the Iceberg milestone:
`IcebergProjectModel` + `forge-doctor iceberg` command group + compat packs.
Spec 113 rewritten in place to match the fuller vision (the original thin
ICE### spec was superseded before implementation).

## Built

- **`analyzers/iceberg_model.py`** — `IcebergEvidence(kind,name,value,file,
  line,source)` + `IcebergProjectModel` (memoized on ctx). Two-pass:
  pass A collects catalogs/configs/runtimes, pass B attributes tables+ops to
  configured catalog prefixes. Sources:
  - AST index call sites: `spark.conf.set` (`spark.sql.catalog.*`,
    `spark.sql.extensions`), any literal arg containing `iceberg`
    (`using("iceberg")`, `format("iceberg")`, iceberg impl class names),
    `writeTo`/`read.table` on configured catalogs.
  - SqlIndex (optional): `CREATE TABLE ... USING iceberg` + TBLPROPERTIES +
    PARTITIONED BY, MERGE/INSERT/DELETE/UPDATE/ALTER, and
    `CALL <cat>.system.<proc>` via statement text (sqlglot Command fallback).
  - `spark-defaults.conf`/`*.properties` (both `k=v` and `k v` forms).
  - IaC: `aws_glue_job`/`AWS::Glue::Job` glue_version → runtime evidence;
    `aws_s3tables_table_bucket`/`aws_glue_catalog_database` → catalog.
- **`SqlStatement.kind`** added (parsed expression type) so consumers don't
  re-parse.
- **`checks/iceberg.py`** — ICE000 anchor, ICE001 format-version vs v2 ops,
  ICE002 unpartitioned MERGE, ICE008/009/010 maintenance gaps
  (expire_snapshots WARNING; rewrite_data_files/rewrite_manifests INFO),
  ICE012 catalog config conflicts, ICE013 Glue runtime compat via the pack.
  Registered unconditionally (works without sqlglot).
- **`cli/iceberg.py`** — `iceberg inspect` (runtime/catalogs/ops/maintenance/
  risks panel), `iceberg maintenance`, `iceberg compatibility`.
- **`knowledge/iceberg/{versions,compatibility}.json`** — schema_version 2,
  sourced, conservative severity floors (Glue 3.0 = HIGH "no bundled
  Iceberg").
- Docs: checks.md Iceberg section, README rows + commands, CHANGELOG 0.8.0,
  SPEC T33 + `iceberg` in the interface list.

## Latent quirks found

- `_dotted()` renders chained calls inside-out (`append(using(df.writeTo))`),
  which mangles `CallSite.name` for chains — op-name normalization added for
  writeTo chains (`dotted.split("(",1)[0]` when name isn't an identifier).
- sqlglot falls back to `exp.Command` for `CALL` — intentional, it's how
  maintenance procs are captured; sqlglot's warning logger is silenced so
  output stays clean.
- sqlglot's own tokenizer gives exact per-statement line numbers for free —
  statements are split on SEMICOLON tokens before parsing (a broken statement
  can't eat its neighbors past the next `;`).

## Verification

- `pytest tests/unit/test_iceberg_model.py tests/unit/checks/test_iceberg.py`
  — 24 + CLI-runner tests green; sql tests still green (29 total new).
- `pytest -x -q` — **459 passed**.
- `ruff check` + `ruff format --check` — clean.
- `mypy src` — 71 files, no issues.
- Manual e2e `iceberg inspect` on a mixed fixture renders the reviewer's
  target shape (Runtime/Catalogs/Operations/Maintenance/Risks).

## Deliberately deferred

`iceberg migrate`, `iceberg merge` deep analysis, partition-cardinality
heuristics, orphan-file risk, SQL→lineage wiring — documented as follow-ups
in the spec's Review Notes.
