# Run: Program O wave 1 — Deep platform ontology (spec 230)

- **Commit**: `83bed03`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/230-deep-platform-ontology.md`

## Scope

Semantic ontology layer on top of the existing resource vocabulary:
platform kinds, workload intents, access patterns, storage semantics,
and the `PlatformKind` → cloud-neutral abstraction bridge.

## Files changed

- `src/forge_doctor/core/platform_ontology.py` — **new** (~990 lines):
  `PlatformKind` (16 kinds), `WorkloadIntent`, `AccessPattern`,
  `ConsistencyModel`, `OwnershipModel`, deterministic registries
  (`workload_intents`, `workload_kinds`, `kind_abstractions`,
  `service_kinds`), and implementation tables (57 services across
  aws/azure/gcp/snowflake/databricks).
- `src/forge_doctor/core/ontology.py` — vocabulary extended with the
  semantic sections (entity kinds, rel kinds, provenance tiers).
- `src/forge_doctor/cli/misc.py` — `ontology` group gained
  `workloads`, `access-patterns`, `platform-kinds`, `implementations`,
  `abstraction-map` (all `--json`).
- `docs/ontology.md` — generated tables appended; version note moved
  to end.
- `tests/unit/test_platform_ontology.py` — **new**, 43 tests.
- `tests/unit/test_ontology.py` — vocabulary-order expectations updated.

## Design decisions

- **Deterministic registries, not inference** — every mapping is a
  declared table; nothing derives a platform kind heuristically.
- **Additive vocabulary** — existing sections unchanged; new sections
  appended in stable order so `ontology vocabulary` output stays
  byte-compatible for prior keys.
- **Docs generated from the module** — tables in `docs/ontology.md`
  are emitted from the registries so they cannot drift.

## Validation

- `pytest tests/unit/test_platform_ontology.py` — 43 passed.
- `pytest tests/unit/ -k ontology` — green incl. doc-parity test.
- ruff + mypy clean.
