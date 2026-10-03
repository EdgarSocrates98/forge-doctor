---
id: 199
title: Public API/SDK Stabilization
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_api.py -x -q
  - python -m forge_doctor_data schema contracts
  - python -c "import forge_doctor_data.api as fd; print(fd.SCHEMA_VERSION)"
---

# Roadmap-2 Phase 8 - Public API/SDK Stabilization

## Context

Adoption needs a stable programmatic contract. Today callers must
reach into internals (`ScanService`, `CheckRunner`, model modules) and
JSON outputs carry ad-hoc `schema_version` literals.

## Acceptance Criteria

- `forge_doctor_data/api.py` — `__all__` is the semver-tracked public
  surface: `scan`, `platform_graph`, `capabilities_evaluate`,
  `what_if`, `migrate_plans`, `version`, `SCHEMA_VERSION`, plus the
  re-exported types `ScanReport`, `ScanOptions`, `DataPlatformGraph`.
- `scan()` runs through `ScanService` (profiles + policy applied),
  offline, no target-code execution.
- `capabilities_evaluate` returns the status string
  (`supported|unsupported|conditional|unknown`).
- `SCHEMA_VERSION` is the single constant behind every
  `schema_version` key in JSON output — central, not literal.
- `core/schemas.py` + `schema contracts [name]` publish JSON Schemas
  for `scan-report`, `policy-pack`, `lab-expected`, `golden-snapshot`.
- `docs/api.md` documents the surface, semver rules, and guarantees
  (offline, deterministic, non-destructive).
- Tests pin `__all__`, schema registry completeness, JSON contract
  required keys, and end-to-end API calls on a fixture.

## Constraints

- Public API is additive only; internal modules stay unversioned.
- `SCHEMA_VERSION` stays `1.0` — this phase publishes the contract,
  it does not change it.
