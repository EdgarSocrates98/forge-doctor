# Run: Program P wave 5 — Optimization intelligence 2.0 (spec 239)

- **Commit**: this commit
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/239-optimization-intelligence-v2.md`

## Scope

Multi-objective optimization layer over the behavioral evidence from
specs 235–238 — extends `core/optimize.py` (v1 untouched) with
`OptimizationOpportunity` carrying benefit + tradeoff + guardrails.

## Files changed

- `src/forge_doctor/core/optimize_v2.py` — **new**: `Objective` (6),
  `OptimizationFamily` (14), `GuardrailStatus` (clear/review/blocked/
  not_applicable), `ExperimentPlan`, `OptimizationOpportunity`,
  `opportunities()` mapping PERF/COST/REL/PHY evidence onto families,
  `_FAMILY_TABLE` (declared expected effects + negative tradeoffs),
  `_CONSTRAINT_FOR_FAMILY` guardrails (freshness/latency/RPO
  objectives → REVIEW), capability-gate via
  `capability_deps.evaluate_dependencies` (→ BLOCKED),
  `opportunity_facts()` → HYPOTHETICAL `TwinFact`s.
- `src/forge_doctor/cli/optimize.py` — `optimize` becomes a Typer
  group; bare invocation keeps the v1 listing (`--path` option now),
  plus `optimize inspect` / `optimize explain <OPP-id>`.
- `tests/unit/test_optimize_v2.py` — **new**, 12 tests.
- `tests/unit/test_optimize.py` — two callsites updated to `--path`.

## Design decisions

- Every opportunity emits expected_effects AND negative_tradeoffs from
  a declared per-family table — no benefit-only candidates.
- Example mappings per spec: skew→distribution_alignment, high scan→
  partition_pruning, snowflake queue→warehouse_sizing, redshift
  queue→wlm_resource_groups, bigquery queue→slot_reservation,
  shuffle/spill→join_strategy, small files→file_sizing.
- Opportunity ids are sha1-derived (deterministic) — `hash()` would
  vary per process.
- Complexity findings (same subject on >=3 engines) classify
  `opportunity`, never error.
- Guardrails: REVIEW when a protected objective exists for the family
  (freshness for stream_trigger, latency for warehouse_sizing, RPO
  for replication/retention); BLOCKED when a declared capability
  prerequisite evaluates blocked.

## Validation

- `pytest tests/unit/test_optimize_v2.py` — 12 passed
- `pytest tests/ -x -q` — full suite
- `mypy` — clean, 259 files
- `ruff check` + `ruff format --check` — clean
- CLI smoke: `optimize --path .`, `optimize inspect .` verified

## Note

`optimize <path>` positional became `optimize --path <path>` — Click
groups resolve subcommands before callback args. Bare `optimize` and
all subcommands work; two test callsites updated.
