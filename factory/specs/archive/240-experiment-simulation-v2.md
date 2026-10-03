---
id: 240
title: Experiment / Simulation Intelligence 2.0 — runtime-informed validation
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "experiment or workload" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 6: Experiment/Simulation v2 (prompt_evo_step8 §Phase 6 + §44-46)

Evolves `core/experiments.py` from findings-diff verdicts to measured
behavior comparison over evidence bundles.

## Acceptance Criteria

- `ExperimentPlan` v2: hypothesis, target, change, workload,
  baseline_metrics, expected_effects, protected_constraints,
  measured_metrics, acceptance.
- Experiment metrics: runtime duration, scan bytes, shuffle bytes,
  spill, file count, median file size, queue time, freshness,
  throughput, error count.
- Synthetic workload generator: uniform, skewed, burst, late-data,
  high-cardinality, many-small-files, hot-key, wide-row,
  deep-nesting, high-fanout — deterministic (seeded), fixture-level.
- Before/after evidence bundles: experiment accepts two artifact
  bundles and compares measured metrics; verdicts SUPPORTED,
  NOT_SUPPORTED, INCONCLUSIVE, CONSTRAINT_VIOLATED (protected
  constraint breach overrides benefit).
- Performance labs under `labs/performance/` (spark-skew,
  iceberg-small-files, bigquery-scan, redshift-redistribution,
  snowflake-queueing, trino-remote-exchange, clickhouse-scan,
  opensearch-shards) — fixture trees with declared expectations;
  add `labs/execution/`, `labs/cost/`, `labs/reliability/`,
  `labs/optimization/` where the phase's findings need ground truth.
- `lab experiment` extended to carry ExperimentPlan + runtime
  comparison output.
- Scenario ground truth extensions: expected_signals,
  forbidden_signals, expected_root_causes, expected_cost_drivers,
  expected_SLA_status, expected_optimization_candidates — additive to
  the lab harness (existing keys keep working).
- Benchmarks: parse+normalize timings for 1k/10k execution summaries
  recorded in the run record (goal, not gate).
- Tests: verdict matrix (supported/not-supported/inconclusive/
  constraint-violated), workload determinism (same seed → same
  output), before/after comparison, lab scenarios green.

## Constraints

- Verdicts derive only from measured metrics + declared expectations;
  no runtime is executed.
