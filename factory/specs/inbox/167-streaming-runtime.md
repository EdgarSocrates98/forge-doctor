---
id: 167-streaming-runtime
title: Streaming stage 9 - progress snapshots, lag, throughput, cost drivers
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_runtime.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 9 of 10. Runtime evidence arrives
as user-exported JSON: `StreamingQueryProgress` snapshots, lag exports.
Forge Doctor never calls AWS/Spark APIs — snapshots only.

# Acceptance Criteria
- `StreamingProgressModel`: parse `StreamingQueryProgress`-shaped JSON
  (single object or batch array): inputRowsPerSecond,
  processedRowsPerSecond, batchDuration, stateOperators (numRowsTotal/
  memoryUsedBytes), sources endOffset/latestOffset, sink numOutputRows.
- `forge-doctor streaming progress <file.json>` renders a
  diagnose-style report: rates, backlog trend across an ordered batch,
  state growth, findings.
- `StreamingLagModel`: source/processing/sink/end-to-end lag slots from
  snapshot fields (Kafka offsets, Kinesis iteratorAge hints).
- New checks: STREAM120 processing slower than arrival (WARNING),
  STREAM121 sustained backlog (WARNING), STREAM122 unstable processing
  rate (INFO), STREAM123 batchDuration exceeds trigger interval
  (WARNING), STREAM030-state-growth variant for observed monotonic
  stateRows increase (WARNING — runtime evidence).
- `forge-doctor streaming cost .` — cost-driver inventory (always-on
  compute, trigger frequency, state size, partitions, workers, shards);
  drivers only, no invented prices.
- knowledge pack: `knowledge/streaming/progress_fields.json`.
- Tests with snapshot fixtures + docs + CHANGELOG.

# Constraints
- Snapshot files validated strictly; malformed → error path, never
  partial inference.
- Latency-tier classification (batch/incremental/microbatch/near-RT/RT)
  emitted only when a stated requirement exists.
- Depends on 159.
