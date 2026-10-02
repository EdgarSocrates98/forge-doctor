---
id: 191
title: What-If Analysis + Deterministic Migration Planning
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_whatif.py tests/unit/adversarial/test_whatif.py -x -q
  - python -m forge_doctor what-if --change glue-version=5.0 <fixture>
  - python -m forge_doctor migrate plan <fixture>
  - python -m forge_doctor knowledge verify
---

# Phase 10 - What-If + Migration Intelligence

## Context

Final phase of the ten-phase Forge Doctor program. Uses the platform
graph, capability engine, architecture contract, and domain packs to
simulate changes without executing anything. No what-if or migration
planning existed before.

## Acceptance Criteria

- `WhatIfChange(target, property, from_, to, assumptions)` +
  `parse_change` for `--change target=value`.
- `evaluate_change(ctx, change)` → `WhatIfReport` with:
  affected_entities (platform-graph ids), capability transitions
  (per-capability status at from → to), compatibility notes from
  domain packs, contract version-pin conflicts, and unknown entries.
- `MigrationPlan(path_id, source_environment, target_environment,
  affected_entities, blockers, warnings, required_changes,
  validation_steps, rollback)`.
- Named migrations, applied only when detected evidence supports them:
  - `glue-4-to-5` (aws_glue_job at 3.x/4.x; pack-driven blockers)
  - `iceberg-v1-to-v2` (iceberg tables without format-version=2)
  - `databricks-runtime-upgrade` (DBR status from runtime pack)
  - `parquet-to-delta` / `parquet-to-iceberg` (parquet writers/files)
  - `streaming-modernize` (queries lacking checkpoint/stable trigger)
  - `lambda-runtime-upgrade` (functions on eol runtimes)
- `forge-doctor what-if --change ...` and `forge-doctor migrate plan`
  CLIs; plans are advisory, never executed.
- Missing pack facts yield UNKNOWN entries, never invented answers.
- Unit + adversarial tests: unknown-version honesty, spoofed
  identifiers, no-mutation guarantees, determinism.

## Constraints

- Deterministic, offline-first; no cloud calls, no execution.
- No product decisions — a plan is a deterministic function of models
  and packs; insufficient evidence → UNKNOWN.

## Review Notes

- `evaluate_change` resolves `from_` from observed model facts; when
  unobserved it reports `unobserved` + an `unknown` entry.
- `lambda-runtime-upgrade` reports `UNKNOWN` target — the
  lambda/runtimes pack lists eol runtimes only, not a current target;
  this is the spec-mandated honest degradation.
- The `what-if` exit code is 1 when any blocker impact is found —
  suitable for CI gating.
