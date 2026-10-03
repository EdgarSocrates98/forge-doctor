---
id: 235
title: Query Execution Model — vendor-neutral normalized execution spine
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "execution or fingerprint" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Phase 1: Query Execution Model (prompt_evo_step8 §Phase 1)

Evolves Forge Doctor Data from "I understand architecture" to "I understand
how this architecture behaves during execution". Depends on the runtime
evidence layer (existing `core/runtime_evidence.py` + adapters).

## Context

Runtime adapters already normalize exported artifacts into
`RuntimeEvidenceModel` (identifiers/executions/metrics/timings/lag).
That model is artifact-shaped, not query-shaped. This phase adds a
single vendor-neutral `QueryExecution` abstraction so downstream
engines (performance, cost, reliability) consume one spine.

## Acceptance Criteria

- `core/execution_model.py` — new module, schema-versioned:
  - `QueryExecution`: execution_id, engine, query_id,
    query_fingerprint, start_time/end_time/duration, queue_time,
    status, stages, inputs, outputs, bytes_read/written,
    rows_read/written, cpu_time, memory_peak, spill_bytes,
    network_bytes, evidence.
  - `ExecutionStage`: id, kind, duration, input/output rows+bytes,
    cpu, memory, spill, shuffle, remote_io, children.
  - `StageKind`: SCAN FILTER JOIN AGGREGATION SORT WINDOW EXCHANGE
    SHUFFLE WRITE READ REMOTE_READ REMOTE_WRITE MATERIALIZE UNKNOWN.
  - `ExecutionJoin`: join_type, strategy (BROADCAST SHUFFLE_HASH
    SORT_MERGE NESTED_LOOP REMOTE UNKNOWN), left/right/output rows,
    redistribution, broadcast, skew, spill.
  - `ExecutionScan`: source, bytes_total/scanned, rows_total/scanned,
    partitions_total/scanned, predicate, pruning, pushdown.
  - `ExecutionMetrics`: scan_amplification, shuffle_amplification,
    output_amplification, spill_ratio, queue_ratio, cpu_efficiency,
    remote_io_ratio — derived ONLY when denominators + evidence exist;
    otherwise UNKNOWN (never an invented ratio).
- Engine adapters (`analyzers/execution_adapters.py` or equivalent):
  Spark (reuse EventLog aggregates), Snowflake QUERY_HISTORY/profile
  exports, BigQuery INFORMATION_SCHEMA.JOBS* exports, Redshift
  STL_QUERY/SVL_* exports, Trino query JSON, ClickHouse
  system.query_log exports. Each maps onto QueryExecution only the
  fields the artifact demonstrably contains.
- Query fingerprinting: structural fingerprint stable across literal
  changes (`id = 10` vs `id = 20` share a fingerprint); normalization
  must not destroy semantics (identifier/table names stay).
- `redacted_sql` on outputs: literals stripped, fingerprint stable;
  secrets never appear in JSON output.
- Execution identity: joins to graph entities only via demonstrable
  identifiers (query id, fingerprint, explicit table refs, job id).
  No fuzzy joins.
- Deterministic ordering, provenance (adapter + artifact path) on
  every execution.
- Tests: per-engine adapter happy path, malformed artifact → empty
  model, missing fields → UNKNOWN metrics, fingerprint stability,
  redaction, serialization round-trip, determinism.

## Constraints

- Offline-first: exported artifacts only, never connect to engines.
- No `os.environ`/home/`which` inside analyzers (hermetic §13).
- Additive: `RuntimeEvidenceModel` API unchanged.

## Review notes

- Open question: whether QueryExecution becomes a graph entity
  (RUNS_ON edge) is deferred — evaluate before inflating RelKind.
