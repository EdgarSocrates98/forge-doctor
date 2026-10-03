# Run: Program J — Optimization Intelligence (spec 229)

- **Initial HEAD**: `b0bebb2`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/229-optimization-intelligence.md`
  (authored earlier this session)

## Scope

Deterministic enumeration of optimization *candidates* from the twin's
inputs (findings + platform graph). Suggest and estimate — never apply.
Disjoint from `advise`: advise ranks problems, optimize ranks
opportunities.

## Files changed

- `src/forge_doctor_data/core/optimize.py` — **new**: `OptimizationCandidate`
  model + `optimize()` enumerator. Two evidence-gated sources:
  1. finding → hypothesis map (`SPARK003`→`partition-data`,
     `STREAM002`→`add-checkpoint`, `PLAT003`→`increase-trigger-interval`);
  2. `platform_versions(graph)` vs compatibility-pack `targets` →
     upgrade candidates with `what-if --change` validation
     (`glue-version-upgrade`; registry is additive per domain).
- `src/forge_doctor_data/cli/optimize.py` — **new**: `optimize <path>`
  (table with conf/reason/cost proxy/entities/validate command,
  `-f json`, `--top N`).
- `src/forge_doctor_data/cli/__init__.py` — registration (Estate & change
  panel).
- `tests/unit/test_optimize.py` — **new**, 13 tests.
- `README.md`, `CHANGELOG.md` — entries.

## Design decisions

- **Confidence from evidence planes** per spec: runtime evidence or a
  CONFIRMED cluster promotion → `high`; derived/observed-metadata →
  `medium`; static only → `low`. `PromotionLevel` is a plain Enum, so
  promotion merge uses `list(PromotionLevel)` ordinal order.
- **Cost proxy is intentionally trivial** — `max(1, blast-radius
  entity count)`; labeled as a proxy everywhere. No pricing tables
  (spec's open question defers calibration until a stale-pricing
  policy exists — kept observed-metadata-only).
- **Validation handoff is a literal command string**: finding-backed
  candidates print `forge-doctor-data lab experiment <path> --hypothesis
  <name>` (the project itself is a valid scenario for `run_experiment`);
  version candidates print `forge-doctor-data what-if --change
  <target>=<to> <path>` where `<to>` is the newest declared pack
  target.
- **Entity attribution via `Entity.file`** — graph entities whose file
  matches the finding's file; version candidates cite all entities of
  the platform domain.
- **Dedupe per (optimization, file)**; a finding with no mapped
  hypothesis and no version signal produces zero candidates — absent
  evidence means absent claims (spec AC).

## Tests / gates

- `pytest tests/unit/test_optimize.py`: **13 passed**
  (fixture-planted candidates, dedupe, deterministic ordering,
  no-candidates-on-clean, entity attribution, confidence mapping,
  glue upgrade + at-latest negative, CLI text/json).
- ruff check/format, mypy: clean on touched files.
- Dogfood: `optimize` on a PySpark fixture prints the partition-data
  candidate with its `lab experiment` validation command.

## Known limitations

- Only `glue` has pack `targets` today — upgrade candidates are
  glue-only until other domains declare targets (additive registry).
- `lab experiment <project>` validates against the whole project copy;
  per-file scenario scoping is future work.

## Open questions (from spec)

- Dedupe against already-planned remediations — not done; advise and
  optimize intentionally don't cross-reference yet.
- Cost-proxy calibration — deferred pending stale-pricing policy
  (shared with spec 212).
- Spec remains in `active/` pending human review.
