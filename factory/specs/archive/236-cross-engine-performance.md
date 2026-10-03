---
id: 236
title: Cross-Engine Performance Intelligence — normalized signals + PERF findings
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "performance or perf or skew or baseline" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 2: Cross-Engine Performance (prompt_evo_step8 §Phase 2 + §10-19)

Normalizes equivalent problems across engines into one signal family.
Depends on 235 (QueryExecution) and the existing evidence/promotion
framework.

## Acceptance Criteria

- `core/performance.py` — new module:
  - `PerformanceSignal` — family, subject (execution/stage/entity),
    observed value, threshold/baseline ref, confidence, evidence.
  - Families: SCAN_AMPLIFICATION, JOIN_AMPLIFICATION,
    DATA_EXCHANGE_AMPLIFICATION, SPILL_PRESSURE, QUEUE_PRESSURE,
    POOR_PRUNING, REMOTE_IO_AMPLIFICATION, SMALL_FILE_AMPLIFICATION,
    LOW_PARALLELISM, HIGH_SKEW, MATERIALIZATION_OVERHEAD,
    WRITE_AMPLIFICATION.
  - `SkewSignal` (execution, stage, max, median, p95, ratio, evidence)
    — only when real task/attempt distribution exists; never inferred
    from code alone as a runtime fact.
  - `QueuePressure` — Snowflake warehouse queue / Redshift WLM /
    BigQuery slot wait / Trino resource-group wait normalized to one
    shape.
  - Amplification ratios derived only with denominators + explicit
    evidence-type labels (proxy vs measured).
- PERF findings PERF001–PERF010 (high scan amplification, poor
  pruning, high data exchange, confirmed skew, spill pressure,
  excessive queue time, remote I/O bottleneck, low parallelism,
  small-file penalty, repeated materialization). Each finding explains:
  observed / derived / threshold applied / evidence. Findings are
  emitted by the performance engine over executions, not registered
  project-scan checks unless wired via the existing check pipeline —
  choose the path that keeps the scan hermetic.
- Finding promotion: STATIC candidate + RUNTIME confirmation →
  promoted, using the existing promotion/root-cause framework (no
  duplicate confidence logic).
- `core/baselines.py` (or performance module): `ExecutionTrend`
  (fingerprint, timestamps, durations, scan, shuffle, spill, queue),
  per-fingerprint baseline (median, p95), regression classes
  NEW/IMPROVED/REGRESSED/STABLE/INSUFFICIENT_DATA; relative regression
  preferred over absolute limits when history exists.
- `core/physical_design.py`: `PhysicalDesign` (partitioning,
  clustering, ordering, distribution, sharding, indexing, replication,
  caching) + per-engine extraction (Snowflake clustering/micro-partition
  pruning, BigQuery partition/cluster, Redshift DISTSTYLE/DISTKEY/
  SORTKEY, ClickHouse PARTITION BY/ORDER BY, OpenSearch
  shards/replicas/routing); PHY001–PHY005 findings.
- `DataGravitySignal`: logical_dataset, physical_representations,
  consumers, clouds, runtime_volume, movement_frequency — facts only,
  no magic score.
- `knowledge/performance/` packs: engine metric semantics, units,
  supported exports, known limitations.
- Tests: signal derivation per family, missing-denominator → no ratio,
  skew only with distributions, promotion path, determinism,
  serialization.

## Constraints

- Thresholds come from contract/pack/baseline/config — no magic global
  numbers; fallback is no finding or informational opportunity.
- Language discipline: "correlated with", not "caused by".
