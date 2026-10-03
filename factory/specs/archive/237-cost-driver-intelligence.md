---
id: 237
title: Cost Driver Intelligence — technical drivers, never billing
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "cost" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 3: Cost Driver Intelligence (prompt_evo_step8 §Phase 3 + §29,59,67)

A taxonomy of technical cost drivers — NOT a billing calculator, never
live pricing, never invoice prediction.

## Acceptance Criteria

- `core/cost_drivers.py` — new module:
  - `CostDriver`: kind, source, value, unit, entity, execution,
    confidence, evidence.
  - Kinds: COMPUTE_DURATION, IDLE_CAPACITY, SCAN_VOLUME,
    STORAGE_VOLUME, NETWORK_EGRESS, CROSS_REGION_TRANSFER,
    REPLICATION, REQUEST_COUNT, INDEX_STORAGE, CACHE_STORAGE,
    SHUFFLE_VOLUME, SPILL_IO, WAREHOUSE_UPTIME, SLOT_USAGE,
    CREDIT_USAGE, RPU_USAGE, EXECUTOR_TIME.
  - Vendor mappings: Snowflake (credits/warehouse uptime/compute
    duration/storage), BigQuery (bytes processed/billed, slot_ms,
    reservations), Redshift (RPU/node time, scan, concurrency
    scaling), Spark/EMR/Databricks (executor/cluster duration,
    workers, shuffle/spill), OpenSearch (nodes/shards/replicas/
    storage).
  - Attribution chain execution → dataset → team → environment; team
    attribution only with ownership evidence.
  - `DataTransferSignal`: source/target cloud+region, bytes, mode,
    evidence.
- COST findings COST001–COST007 (idle compute evidence, repeated high
  scan volume, cross-cloud movement, excessive replication footprint,
  materialization duplication, high shuffle/spill driver, tiny-file
  overhead driver). Same explainability contract as PERF.
- Migration cost deltas: compare driver *kinds* source→target (e.g.
  credit-driven vs scan+slot-driven); never claim "target cheaper".
- `knowledge/cost_drivers/` packs — driver semantics per vendor, no
  pricing.
- Tests: driver extraction per vendor, attribution rules (no team
  without evidence), COST findings, determinism, serialization.

## Constraints

- No monetary amounts without explicit pricing/config evidence in the
  repo — report units (bytes, slot_ms, credits) not dollars.
