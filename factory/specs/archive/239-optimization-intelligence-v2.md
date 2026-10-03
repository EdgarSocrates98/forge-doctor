---
id: 239
title: Optimization Intelligence 2.0 — multi-objective, guardrailed candidates
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "optimiz" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 5: Optimization Intelligence 2.0 (prompt_evo_step8 §Phase 5 + §26,61,69)

Extends `core/optimize.py` from finding-driven candidates to
multi-objective optimization grounded in execution/performance/cost/
reliability evidence. Never auto-applies.

## Acceptance Criteria

- `OptimizationOpportunity` (extend or parallel model): id, target,
  objective, evidence, expected_effects, negative_tradeoffs,
  protected_constraints, confidence, prerequisites, experiment_plan,
  unknowns. Every opportunity presents benefit AND tradeoff — never
  benefit alone.
- Objectives: PERFORMANCE, COST_DRIVER, RELIABILITY, FRESHNESS,
  COMPLEXITY, PORTABILITY.
- Guardrails: candidates consult SLA objectives, criticality,
  capability graph (blocked required capability → candidate
  NOT_APPLICABLE or BLOCKED), reliability constraints.
- Optimization families: partition pruning, distribution alignment,
  join strategy, parallelism, file sizing, stream trigger, warehouse
  sizing, slot/reservation usage, WLM/resource groups,
  materialization, replication, index/shard layout, retention, cache.
- Example mappings wired to real evidence: Spark confirmed skew →
  partition strategy experiment; BigQuery repeated high scan →
  partition/filter/materialization; Snowflake high queue + low
  concurrency → compute/concurrency experiment; Redshift
  distribution-heavy joins → distribution redesign; OpenSearch tiny
  shards → shard consolidation.
- Complexity opportunities (same logical dataset on N platforms, same
  workload on N schedulers, duplicate CDC) classified OPPORTUNITY, not
  ERROR.
- `optimize inspect` / `optimize explain` CLI additions showing
  expected vs negative effects, guardrail status, unknowns.
- Five-state twin: optimization candidates produce HYPOTHETICAL
  facts.
- Tests: per-family eligibility, guardrail blocking via capability
  graph, tradeoff presence, OPPORTUNITY classification, determinism,
  serialization.

## Constraints

- Candidates carry `validate_command`/`experiment_plan` — the engine
  proposes, labs measure; nothing deploys.
