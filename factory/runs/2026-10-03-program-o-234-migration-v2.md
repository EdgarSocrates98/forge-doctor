# Run: Program O wave 5 — Migration intelligence v2 (spec 234)

- **Commit**: `ebf5945`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/234-migration-intelligence-v2.md`

## Scope

Semantic migration layer on top of the wave-7 plan builder:
cloud-neutral concepts, explicit mapping quality, lossiness,
capability deltas, evidence completeness, readiness, SQL portability,
and schema compatibility.

## Files changed

- `src/forge_doctor/core/migration_v2.py` — **new**: concept registry;
  `map_service` → `MigrationConcept` (`MappingKind` direct /
  approximate / redesign_required / no_equivalent / unknown,
  `Lossiness`, `SemanticChange`, capability gaps, missing evidence);
  `assess_readiness` → `MigrationReadiness` (ready / partial /
  blocked / insufficient_evidence); `detect_runtime_sources`.
- `src/forge_doctor/core/sql_portability.py` — **new**: dialect
  registry + `SQLPORT001`-`SQLPORT007` findings (source-exclusive
  functions, joins, DDL shapes, transaction semantics).
- `src/forge_doctor/core/schema_compat.py` — **new**: normalized
  type-family mapping with dialect-specific nested shapes;
  classifications direct / coerced / lossy / incompatible / unknown.
- `src/forge_doctor/core/crossmigration.py` — additive plan fields
  `concepts`, `readiness`, `sql_findings`; concept mapping per entity
  map; azure/gcp/aws `_ECOSYSTEM` targets; `.sql` files scanned when
  both dialects known.
- `src/forge_doctor/cli/whatif.py` — `migrate explain`; plan and
  explain JSON output normalize enums to values.
- `docs/checks.md` — SQLPORT findings documented beside MIGR.
- `tests/unit/test_migration_v2.py` — **new**, 34 tests.
- Typing hardening landed here: `capability_deps.py`,
  `twin_states.py`, `cli/twin.py`, `cli/misc.py` (mypy strict:
  19 residual errors from waves 2–3 fixed).

## Design decisions

- **Same-service short-circuits to DIRECT** — declared capability
  gaps describe migrating *to* a platform, not staying on it.
- **Unknown ≠ unsupported** — `no_equivalent` requires a declared
  negative; absent evidence → `unknown` + `missing_evidence`.
- **Lossy direction is explicit** — e.g. `STRUCT`→flat tuple is
  lossy while `OBJECT`/`VARIANT`→`JSON` is coerced; the mapping
  table carries per-direction semantics, not a symmetric guess.
- **SQLPORT findings are plan-scoped** — like MIGR, they are not
  registered project-scan checks; `--format json` serializes enums
  as values.

## Found & fixed en route

- Initial run mis-keyed `eventhub` — abstraction registry uses
  `eventhubs`.
- `VARIANT→JSON` and `OBJECT→JSON` classified lossy; corrected to
  coerced (cross-family entry added); the true lossy case is
  `STRUCT`→tuple-like.
- `Tuple`→OpenSearch `object` is direct — test aligned to intended
  semantics.
- mypy strict surfaced `list`/`dict`/`object` leftovers across waves
  2–5 files — all fixed without behavior change.

## Validation

- `pytest tests/unit/test_migration_v2.py` — 34 passed.
- `pytest tests/ -x -q` — **1914 passed**.
- `mypy` — clean on 249 source files.
- `ruff check` + `ruff format --check` — clean.
- Concept mapping smoke: kinesis→pubsub approximate/low,
  dynamodb→bigtable approximate (DYNAMODB_GSI/STREAMS gaps),
  emr→dataproc approximate/medium.

## Open items / boundaries

- SQL portability is dialect-table + lexical scan — no sqlglot
  transpilation (deferred per wave-7 note).
- Concept registry covers declared mappings only — services without
  evidence land `unknown`, honestly.
