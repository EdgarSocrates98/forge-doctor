# Run: Program K wave 7 — Cross-platform migration (spec 224)

- **Initial HEAD**: (after `cloud` wave-6 commit)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/224-cross-platform-migration.md`
- **Depends on**: 212 (warehouse), 213–215 (vendor adapters), 223
  (abstractions) — all landed; spec constraint satisfied.

## Scope

`migrate plan --from <platform> --to <platform>` and
`what-if --change platform=<target>` — deterministic, advisory,
report-only cross-platform plans on the abstraction layer.

## Files changed

- `src/forge_doctor_data/core/crossmigration.py` — **new**:
  `PlatformMigrationPlan` (`EntityMapping`/`CapabilityDelta`/
  `StagePlan`), `_ECOSYSTEM` target-service maps per platform
  (bigquery/redshift/synapse/snowflake/databricks), `_CHANGE_NOTES`
  per (service, abstraction), `_capability_deltas` over the knowledge
  packs, staged plan ordering (catalog→schema→data→compute→consumers),
  `_unmapped_consumers` via graph READS*/DEPENDS_ON edges, and
  `_migr_findings` emitting MIGR001/002/003 `CheckResult`s.
- `src/forge_doctor_data/core/whatif.py` — `platform`/`warehouse` change
  targets delegate to `_platform_change_report` (source auto-detected
  from the single warehouse-platform service; MIGR findings become
  WhatIfImpacts, lost/gained surface in `unsupported_now`/
  `supported_now`).
- `src/forge_doctor_data/cli/whatif.py` — `migrate plan` gained
  `--from/--to` + `--format json`; text render shows entity map,
  deltas, stages, findings; exits 1 on error-severity findings.
- `src/forge_doctor_data/analyzers/abstractions.py` — warehouse-domain
  vendor objects (stage/stream/task/pipe kinds) now fold into
  `object_storage`/`stream`/`compute_engine` abstractions so snowflake
  estates map end-to-end.
- `docs/checks.md` (MIGR section), `README.md`, `CHANGELOG.md`.
- `labs/migration/snowflake-to-bigquery/` — snowflake TF + DDL
  (stage/stream/task) fixture.
- `tests/unit/test_migration.py` — **new**, 12 tests.

## Design decisions

- **Plans are reports** — `PlatformMigrationPlan` serializes to JSON;
  MIGR findings are `CheckResult`s (category `migration`) but are
  plan-scoped, not registered as project-scan checks (spec: "checks on
  plans").
- **Ecosystem maps are honest about gaps** — snowflake as a *target*
  has no `operational_store` mapping; unmapped → MIGR001 blocker rather
  than fabricated equivalents.
- **Delta semantics** — `lost` requires explicit `unsupported` on the
  target pack; `unknown`/absence resolves to `review` (MIGR002) — an
  honest "needs human check", never a silent assumption.
- **Source detection** — `what-if platform=X` takes the detected
  warehouse platform when exactly one exists; ambiguity/ none falls
  back to the explicit `from_`/`unknown`.
- **Consumer unmapping** — `READS`/`READS_FROM`/`INVOKES`/`DEPENDS_ON`
  into migrated entities; same-domain consumers (sql/warehouse/
  metadata/quality) excluded since they're the migrating surface.
- **No transpiler claims** — per the spec's open question: structural
  notes + callouts only; sqlglot transpilation deferred.

## Found & fixed en route

- `CheckResult` requires `title`/`category`/`recommendation` (no
  `why`/`fix` kwargs) — findings rewritten against the real schema.
- `Severity` has no `MEDIUM`/`HIGH` — MIGR001→`ERROR`, MIGR002/003→
  `WARNING` (severity bracket `[warning]` also collided with Rich
  markup — printed as plain text now).
- cp1252 consoles can't encode `→` — new output (and the two legacy
  `→` prints in whatif) switched to `->`.
- `snowflake` warehouse objects weren't folding — `_scan_graph` only
  folded `kind==warehouse`; `_WAREHOUSE_KIND_ABSTRACTION` now covers
  stage/stream/task/pipe kinds.

## Validation

- `pytest tests/unit/test_migration.py` — 12 passed, including the
  snowflake→bigquery lab end-to-end: entity map (wh/db→bigquery,
  stage→gcs, stream→pubsub, task→dataproc), TIME_TRAVEL equivalent,
  BIGLAKE_EXTERNAL gained, ZERO_COPY_CLONE review, MIGR002 findings.
- `what-if --change platform=bigquery` auto-detects snowflake source,
  reports 5 affected entities, gained capabilities, review list.
- `migrate plan -f json` produces a clean serializable document.
- Lab `snowflake-to-bigquery` passes; 42 labs total green.
- ruff + mypy clean on touched files.

## Open items / boundaries

- Transpilation (sqlglot dialect rewrite) deferred — separate spec.
- `synapse`/`databricks` targets have sparse capability packs — deltas
  resolve `review`/`unknown` honestly rather than fabricating.
- MIGR003 coverage depends on graph READS* edges — consumers outside
  the graph (external BI tools) can't be seen.
