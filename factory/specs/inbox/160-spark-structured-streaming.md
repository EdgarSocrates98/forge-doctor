---
id: 160-spark-structured-streaming
title: Streaming stage 2 - Spark SS depth (triggers, joins, state store)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming.py tests/unit/test_streaming_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 2 of 10. Extends 159's model with
full Spark Structured Streaming semantics: trigger kinds, join
classification, state-store provider config.

# Acceptance Criteria
- Model gains: trigger kind (processingTime/availableNow/once/
  continuous/realTime + interval literal), join detection with
  stream-stream vs stream-static classification (both sides
  `isStreaming`-derived where possible; else `unknown` — honest),
  state-store evidence (`spark.sql.streaming.stateStore.providerClass`,
  `spark.sql.streaming.stateStore.rocksdb.*`, async-checkpoint confs).
- New checks: STREAM090 trigger suspiciously aggressive for observed
  workload shape (INFO, heuristic), STREAM091 processingTime interval
  vs stated latency hints (INFO), STREAM092 AvailableNow on a query the
  surrounding evidence frames as continuous (INFO), STREAM040 large
  stateful workload on default state store (INFO), STREAM041 RocksDB
  candidate (INFO), STREAM042 inconsistent RocksDB/state-store confs
  (WARNING), STREAM050 stream-stream join without watermark (WARNING),
  STREAM051 join state potentially unbounded (INFO), STREAM060
  dropDuplicates without watermark (INFO).
- knowledge pack additions: `knowledge/streaming/spark/triggers.json`
  (trigger kinds + semantics), state-store provider notes.
- Tests per check; docs checks.md + CHANGELOG.

# Constraints
- Join stream/static classification must degrade to `unknown` rather
  than guess — `isStreaming` is a runtime property.
- RocksDB checks are candidates/recommendations, never "must use".
- Depends on 159.
