# Phase 10 run — What-if + Migration Intelligence

- Spec: `factory/specs/active/191-whatif-migration.md`
- Commit message: `feat(migration): add what-if analysis and deterministic migration planning`

## Implemented

- `core/whatif.py` — `WhatIfChange(target, property, from_, to,
  assumptions)`, `parse_change("glue-version=5.1")`,
  `evaluate_change(ctx, change)` → `WhatIfReport`:
  - affected entities from the platform graph (domain-scoped);
  - capability transitions via `capability_registry().evaluate()` at
    `from` vs `to` (lost caps → blocker impacts, gained → enablers,
    conditional → warnings, unmatched → unknown);
  - compatibility notes from domain packs (`glue/compatibility`
    targets, `iceberg/compatibility` runtimes, `databricks/runtime`,
    `lambda/runtimes`, `iceberg/versions`);
  - contract conflicts (pipeline `compute.version` pins ≠ `to` →
    pre-drifted ARCH002 warning);
  - honest `unknown` entries when facts are missing.
- `core/migration.py` — `MigrationPlan` + `plan_migrations(ctx)`
  with seven named paths: `glue-4-to-5`, `iceberg-v1-to-v2`,
  `databricks-runtime-upgrade`, `parquet-to-delta`,
  `parquet-to-iceberg`, `streaming-modernize`,
  `lambda-runtime-upgrade`. Each reports source/target envs,
  affected entities, blockers, warnings, required changes,
  validation steps, rollback. Advisory only — no execution verbs.
- `cli/whatif.py` — `what-if --change target=value [--assume fact]`
  (exit 1 on blockers, for CI gating) and `migrate plan` attached to
  the pre-existing `migrate` group from `cli/compatibility.py`
  (existing `migrate glue` command preserved).

## Key decisions

- `what-if` resolves `from_` from observed model facts (TF glue
  versions, iceberg `format-version` properties, DBR versions, lambda
  runtimes, EMR releases); when nothing is observed the report shows
  `unobserved` + an `unknown` entry — never a fabricated baseline.
- The capability diff treats `UNKNOWN` transitions as warnings, not
  silent passes: `no capability facts for platform X` lands in
  `report.unknown`.
- `lambda-runtime-upgrade` reports target `UNKNOWN` — the
  `lambda/runtimes` pack only lists eol runtimes; the spec's
  honest-degradation rule applies.
- A second `migrate` Typer group collided with the existing
  `migrate glue` command (caught by `test_migrate_glue_cli`) — fixed
  by attaching `migrate plan` to the shared `migrate_app` from
  `cli/compatibility.py`.

## Verification

- `pytest tests/unit/test_whatif.py tests/unit/adversarial/test_whatif.py -q`
  → 24 passed
- `pytest -q` → 1196 passed, 0 failed
- `mypy src` → clean (141 files)
- `ruff check src tests` + `ruff format` → clean
- `forge_doctor_data knowledge verify` → all 74 packs ok
- CLI smoke: `what-if --change glue-version=5.0` on a glue-4.0 fixture
  shows the glue job + LF-capability transition + pack-driven Java/Python
  blockers; `migrate plan` lists glue-4-to-5, iceberg-v1-to-v2,
  lambda-runtime-upgrade, parquet-to-delta, parquet-to-iceberg;
  `migrate glue --from 4.0 --to 5.0` (pre-existing) still passes.

## Open questions

- None blocking — spec stays `active/` for human review.
