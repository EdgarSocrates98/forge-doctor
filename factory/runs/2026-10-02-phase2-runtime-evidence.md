---
spec: 183-runtime-evidence
phase: prompt_evo_next_step phase 2 (Runtime Evidence)
date: 2026-10-02
agent: devin
---

# Phase 2 — Runtime Evidence Ingestion Framework

## What was built

- `core/runtime_evidence.py` — `RuntimeEvidenceModel` (executions, events, metrics,
  errors, timings, throughput, lag, retries, resource_usage, state, identifiers),
  `RuntimeExecution`, `ExecutionMetric`, `ExecutionError`, `ExecutionTiming`,
  `ExecutionThroughput`, and the `RuntimeEvidenceAdapter` protocol.
- `analyzers/runtime_evidence.py` — seven deterministic, offline-only adapters:
  - `SparkEventLogAdapter` — JSONL `SparkListener*` events: jobs, stages, task
    metrics (duration, shuffle r/w, spill, GC, input/output), executor loss,
    skew signals (max vs median task duration).
  - `StructuredStreamingProgressAdapter` — `progress.json`: input/processed
    rates, batch duration, stateOperators, source offsets (nested topic→partition
    flattened deterministically), sink, eventTime/watermark.
  - `GlueLogAdapter` — job/run identifiers, executor loss, OOM, known Glue/Spark
    error lines only (no OCR-style heuristics).
  - `AthenaStatsAdapter` — DataScannedInBytes + queue/planning/execution/service
    timings.
  - `LambdaReportAdapter` — duration, billed duration, memory size, max memory
    used, init duration, request id.
  - `StepFunctionsHistoryAdapter` — state transitions, retries, failures,
    timeouts, execution duration, failed states.
  - `NeptuneExplainAdapter` — feeds `neptune_explain` reports into the common
    model (flags → errors, cardinality → metrics).
- `ingest_artifact(path)` dispatch: ordered `sniff()` match, tolerant of
  malformed/unreadable input (returns `source=unreadable` model, never raises).
- `cli/runtime.py` — `runtime inspect <artifact>` (normalized fact dump,
  `--format json`) and `runtime diagnose <artifact>` (extracted errors joined
  against knowledge-pack signatures).
- `cli/streaming.py` — `streaming progress progress.json` with backlog warning
  when processed < input rate.

## Identity + evidence policy

Runtime→static joins use only exported identities (ARN, job name, query id,
execution id, stream name). No fuzzy matching. Unknown/missing fields surface
as absent facts, not negative claims. All parsing is local-file only.

## Verification

- `pytest tests/unit/test_runtime_evidence.py` — 16 passed (adapter fixtures,
  malformed input, determinism).
- `pytest -x -q` — 996 passed.
- `mypy src` — clean (113 files); `ruff check src tests` — clean;
  `ruff format --check` — clean.
- CLI smoke: `runtime inspect` on streaming progress shows identifiers, batch
  execution, throughput, state rows, watermark, flattened partition offsets;
  `runtime diagnose` on a Lambda REPORT parses cleanly and honestly reports
  "no errors extracted, no signatures matched"; `streaming progress` prints the
  backlog warning when processed rate < input rate.

## Open questions

None — adapter set covers the phase contract. Deeper correlation of runtime
facts with static findings is Phase 3 (Finding Promotion).
