# Run: 129-iceberg-deep (Iceberg stage 2)

- Spec: `factory/specs/active/129-iceberg-deep.md` (staged from inbox, grill: completed)
- Agent: devin
- Source prompt: `prompt_evo_databricks_emr.md` — Iceberg-first ordering chosen by reviewer.

## Implemented

- `SqlStatement` gains `merge_source` + `merge_on_cols` (sqlglot `exp.Merge`
  `using`/`on` args; target-side cols filtered by target name/alias).
- `IcebergProjectModel` new evidence: `merge_detail` (target→source),
  `merge_on` (target→col), `partition.<col>` transform properties,
  `write_api` (v2/legacy/legacy_catalog), `write_pattern`
  (repartition/coalesce near writes), `catalog_type` (post-pass impl
  classification across all evidence sources).
- New checks ICE020, ICE021, ICE022, ICE023, ICE024, ICE025 (feature floors
  driven by `knowledge/iceberg/spec.json`, schema_version 2).
- `forge-doctor iceberg merge` + `iceberg files` subcommands.
- New inbox specs written for the remaining platform phases:
  130-emr-doctor, 131-databricks-doctor, 132-platform-capability,
  133-lakeformation-deep.

## Latent quirks found

- `CallSite.name` for chained calls (`a.f().g()`) is corrupted by inside-out
  `_dotted` rendering (`'repartition)'`) — extracted `_call_name()` helper;
  `_operation_evidence` had the same normalization inlined, now shares it.
- `PARTITIONED BY (days(ts), bucket(16, id))` needs balanced-paren parsing —
  `[^)]*` regex truncated at the inner paren and `bucket` column arg is last,
  not first.
- `CallSite.args` only captures string literals — `repartition(1)` appears
  as a bare name, so ICE024 is an honest static heuristic, not a count.
- Catalog impl classification initially emitted only from python conf.set —
  moved to a post-pass so `spark-defaults.conf`/`*.properties` catalogs
  classify too.

## Verification

- `pytest`: 545 passed (was 528; +17 tests)
- `ruff check`, `ruff format --check`: clean
- `mypy src`: clean (77 files)
- E2E: `iceberg merge .pytest_tmp/e2e` reconstructs target/source/ON-cols/
  partition-predicate verdict; `iceberg files` renders static-risk posture;
  scan fires ICE021-024 on fixture.

## Gate

Spec left in `active/` — awaiting human review + `archive --accepted`.
