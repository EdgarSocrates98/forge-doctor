---
id: 163-streaming-glue
title: Streaming stage 5 - Glue Streaming model + Glue 6 Real-Time Mode
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_glue.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 5 of 10. Glue 6.0 adds Real-Time
Mode (`Trigger.RealTime`) for sub-second latency on supported
Structured Streaming workloads; Glue streaming sources are
Kinesis/MSK/Kafka, sinks include S3/JDBC/Iceberg/Delta/Hudi. Glue
version evidence already exists via the glue analyzer.

# Acceptance Criteria
- `GlueStreamingJob` facts on query records: glue version (existing
  glue model), workers/worker type when visible, real_time_mode flag
  (`Trigger.RealTime`/`processingTime` sub-second), streaming source /
  sink linkage.
- New checks: GLUESTR001 `Trigger.RealTime`/RTM markers on Glue < 6.0
  (WARNING — version evidence from glue model), GLUESTR002 low-latency
  requirement evidence with microbatch trigger on Glue 6 candidate
  (INFO), GLUESTR003 RTM marker with unsupported workload shape
  evidence (INFO), GLUESTR010 streaming Glue job anchor, GLUESTR012
  source outside Kinesis/MSK/Kafka (INFO), GLUESTR013 sink outside the
  documented supported set (INFO).
- knowledge pack: `knowledge/streaming/glue/streaming.json` —
  RTM feature floor (>= 6.0), supported sources/sinks, sources list.
- Tests + docs + CHANGELOG.

# Constraints
- Version comparisons only when glue version evidence exists; unknown
  version → no GLUESTR001 (never guess).
- RTM suitability checks are candidates, not verdicts.
- Depends on 159/160/162.
