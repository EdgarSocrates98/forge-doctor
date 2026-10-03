# Program Q — Wave 6 Run Record: Spec 246 Capacity / Saturation

Date: 2026-10-03 · Spec: `factory/specs/active/246-capacity-saturation-intelligence.md`

## Delivered

- `src/forge_doctor/core/capacity.py`
  - `CapacityDimension` — CPU / MEMORY / CONCURRENCY / SLOTS /
    WAREHOUSE_LOAD / SHARDS / PARTITIONS / EXECUTORS / WORKERS /
    THROUGHPUT / STORAGE / REQUEST_RATE / QUEUE.
  - `CapacitySignal(resource, dimension, configured_capacity,
    observed_usage, saturation, headroom, history, threshold, unit,
    evidence)` — bounded history window (last 50 observations), native
    units preserved.
  - `SaturationClass` HEALTHY / ELEVATED / SATURATED / UNKNOWN.
  - `CapacityThreshold` with mandatory `ThresholdProvenance`:
    CONFIG (`saturation_*` attrs) > PACK (`knowledge/capacity/<engine>.json`)
    > BASELINE (p95/median distribution rule, >=8 points) > none -> UNKNOWN.
    No global ratio defaults.
  - `CapacityTrend` — rising/falling/flat/insufficient_data + headroom;
    linear extrapolation labelled `simple_projection`, never "prediction".
  - `capacity_signals` / `capacity_trends` / `capacity_findings` —
    CAP001 queue, CAP002 memory, CAP003 storage-layout
    (partitions/shards/storage), CAP004 workers/executors, CAP005
    concurrency/slots/warehouse-load/request-rate, CAP006 trend
    increasing, CAP007 low headroom (<20%) on critical workloads.
    Unthresholded-but-observed dimensions emit an INFO "unverifiable"
    note under the dimension's finding id.
- `src/forge_doctor/knowledge/capacity/` — packs for snowflake,
  bigquery, redshift, trino: documented queue field semantics (original
  units preserved) + saturation ratio cutoffs where the platform
  documents them.
- `runtime capacity` CLI (under the existing `runtime` group): signals +
  trends + findings, `--json`; ASCII-only output.
- `src/forge_doctor/analyzers/execution_adapters.py` — the Snowflake
  adapter now parses `start_time`/`end_time` (real QUERY_HISTORY
  fields); without timestamps no temporal trend was possible.
- Lab harness: `expected_capacity_signals`
  ("<subject>.<dimension>=<class>", `*` wildcard), 
  `expected_capacity_findings` (CAP ids), `capacity_attrs` (declared
  configured capacity per subject, mirrors user config).
- `labs/capacity/queue-saturation/` — Snowflake queue ramp
  300ms -> 600ms -> 900ms against max_queue_ms=1000: final signal
  SATURATED, findings CAP001 + CAP006. PASS.
- `docs/checks.md` — CAP001-007 section with provenance explanation.

## Honest-position notes

- Saturation requires *some* evidence: a configured capacity or a
  threshold source. A flat series supplies neither, so saturation stays
  UNKNOWN rather than being called "healthy".
- Queue normalization across engines stays conceptual: the QUEUE
  dimension maps to `queue_time_ms` regardless of engine, but the pack
  records each engine's raw field names and units.
- Spec says thresholds are "contract/config > platform pack >
  historical baseline"; a configured capacity without any ratio cutoff
  still cannot classify by itself — ratios come from the same ordered
  sources.

## Validation

- `pytest tests/unit/ -k "capacity or saturation"` — 17 passed.
- Lab: `capacity` scenario PASS.
- `ruff check`, `ruff format --check`, `mypy` (270 files) — clean.
- Full suite: **2112 passed** in 300.12s
