# Run: Program P wave 1 — Vendor-neutral query execution model (spec 235)

- **Commit**: `f5a5284`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/235-query-execution-model.md`

## Scope

Normalized `QueryExecution` spine so downstream performance, cost,
reliability and optimization features consume engine-agnostic
execution records instead of raw exported artifacts.

## Files changed

- `src/forge_doctor_data/core/execution_model.py` — **new**:
  `QueryExecution`, `ExecutionStage` (`StageKind`), status enum,
  scan/shuffle/spill/network/cpu metrics, query fingerprinting
  (normalized, literal-redacted SQL), deterministic serialization.
- `src/forge_doctor_data/analyzers/execution_adapters.py` — **new**: offline
  adapters for Spark eventlog/listener exports, Snowflake
  QUERY_HISTORY, BigQuery INFORMATION_SCHEMA jobs, Redshift
  STL/SVV exports, Trino query JSON, ClickHouse query_log. Explicit
  identity keys only (query_id/execution_id/job name); no fuzzy joins;
  unit-aware duration parsing (Trino `"2.5s"`).
- `src/forge_doctor_data/cli/runtime.py` — `runtime executions` command
  (table + JSON).
- `tests/unit/test_execution_model.py` — **new**, 19 tests: adapter
  parsing per engine, fingerprint stability, redaction, missing
  denominators stay absent, determinism, serialization round-trip.

## Design decisions

- Adapters map demonstrable fields only; absent metrics stay `None`
  rather than inferred.
- Snowflake fallback matcher requires two distinct fields so it can't
  claim a ClickHouse row on `query_id` alone.
- Fingerprint redacts literals before hashing so PII never enters
  fingerprints.

## Validation

- `pytest tests/unit/test_execution_model.py` — 19 passed
- `ruff check` + `ruff format --check` — clean
- `mypy` — clean on new modules
