---
id: 243
title: Change → Runtime Correlation — deterministic evidence-path matching
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "correlat or change" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 3: Change → Runtime Correlation (prompt_evo_step9 §PHASE 3, §11–12, §61, §67, §92)

Connect semantic/architecture/config/deployment change with runtime
behavior change. Reuse `core/change_intel.py` taxonomy — never a
parallel one.

## Acceptance Criteria

- `ChangeEvent` (id, timestamp, source, changed_entities,
  changed_properties, change_class, commit, evidence) — from repo
  snapshots/git, CI deploys, TF apply summaries, dbt runs, Databricks
  bundle deploys when artifacts exist locally; no remote CI access.
- Change classes reused/extended from `change_intel`: RUNTIME_UPGRADE,
  SCHEMA_CHANGE, PARTITION_CHANGE, DISTRIBUTION_CHANGE, CONFIG_CHANGE,
  SECURITY_CHANGE, ORCHESTRATION_CHANGE, CAPACITY_CHANGE, QUERY_CHANGE,
  MATERIALIZATION_CHANGE, DEPENDENCY_CHANGE.
- `ChangeRuntimeCorrelation` (change, regression, temporal_distance,
  graph_distance, shared_entities, matching_dimensions, confidence,
  explanation).
- Confidence rises when: change precedes regression AND same entities
  AND graph path exists AND metric dimension relevant to changed
  property. Configurable windows (±5m/±30m/±2h/±1d), never global.
- Language: "correlated with" / "preceded by" — never "caused" without
  a deterministic evidence chain. Docs-only change + regression → no
  meaningful correlation.
- `PlanFingerprint` + plan-change kinds (JOIN_STRATEGY_CHANGED,
  SCAN_PATH_CHANGED, EXCHANGE_ADDED/REMOVED, PARALLELISM_CHANGED,
  MATERIALIZATION_CHANGED); `DataShapeSnapshot` (rows/bytes/files/
  partitions/cardinality/skew) so data growth isn't blamed on code.
- Score breakdown exposed: temporal match / graph overlap / entity
  overlap / metric relevance.
- CLI: `runtime correlate`, `diff runtime-impact`.
- Labs: `labs/regression/` (baseline stable → partition change →
  PERFREG + correlation + candidate cause) and a false-positive lab
  (docs-only commit + regression → no correlation).

## Constraints

- TimestampQuality: without clock alignment, no high-confidence
  temporal correlation.
