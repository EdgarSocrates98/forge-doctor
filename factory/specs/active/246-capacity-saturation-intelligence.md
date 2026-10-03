---
id: 246
title: Capacity / Saturation Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "capacity or saturation" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 6: Capacity / Saturation Intelligence (prompt_evo_step9 §PHASE 6, §64, §89–90, §95)

Detect signals of approaching limits. No probabilistic forecasting.

## Acceptance Criteria

- `CapacitySignal` (resource, dimension, configured_capacity,
  observed_usage, saturation, headroom, history, evidence) for
  dimensions CPU/MEMORY/CONCURRENCY/SLOTS/WAREHOUSE_LOAD/SHARDS/
  PARTITIONS/EXECUTORS/WORKERS/THROUGHPUT/STORAGE/REQUEST_RATE.
- Saturation classes HEALTHY/ELEVATED/SATURATED/UNKNOWN with thresholds
  from contract/config > platform pack > historical baseline — provenance
  required (no `CPU > 80 = bad` globals).
- `CapacityTrend` — rising utilization shown as trend + headroom;
  optional linear extrapolation labelled "simple projection", never
  "prediction".
- Queue saturation normalized conceptually across Snowflake/Redshift/
  BigQuery/Trino preserving original units.
- Storage-layout saturation: small files / shards / partitions /
  metadata objects.
- Findings CAP001–CAP007 (queue/memory/storage-layout/worker/
  concurrency saturation, capacity trend increasing, low headroom on
  critical workload).
- `knowledge/capacity/` pack only where genuinely needed.
- CLI surface on existing groups (`runtime` or `capacity` under an
  existing group per §36 — no new top-level).
- Lab: `labs/capacity/` queue 30%→60%→90% scenario.

## Constraints

- Explainability: configured capacity + observed usage + historical
  trend always shown.
