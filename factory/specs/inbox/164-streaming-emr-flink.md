---
id: 164-streaming-emr-flink
title: Streaming stage 6 - EMR streaming workloads + FlinkProjectModel
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_emr.py tests/unit/test_flink_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 6 of 10. EMR runs Spark SS, Flink,
and Kafka clients; Flink is the first-class non-Spark engine.

# Acceptance Criteria
- `FlinkProjectModel` (stdlib AST for PyFlink + lightweight Java
  text-scan facts): `StreamExecutionEnvironment`/`enableCheckpointing`/
  `getCheckpointConfig`, `env.fromSource`, `KeyedProcessFunction`,
  windows, state backend config, parallelism, sinks, exactly-once
  markers. `cli/flink.py` `forge-doctor-data flink inspect`.
- EMR: streaming_workload flag on the EMR model when an EMR-flavored
  project contains streaming evidence (depends on 130 EMR model if
  built; degrade gracefully when absent).
- New checks: FLINK001 checkpointing absent (WARNING), FLINK002 state
  backend risk (INFO), FLINK003 savepoint/upgrade path absent (INFO),
  FLINK004 operator parallelism bottleneck hint (INFO), FLINK005
  exactly-once expectation vs sink evidence (INFO); EMRSTR001 executor
  layout poor for continuous workload (INFO), EMRSTR002 dynamic
  allocation on streaming (WARNING), EMRSTR003 Spot on long-lived
  streaming (WARNING), EMRSTR004 auto-termination vs continuous stream
  (WARNING).
- knowledge packs: `knowledge/streaming/flink/model.json`,
  `emr/streaming.json`.
- Tests + docs + CHANGELOG.

# Constraints
- Java sources scanned textually for Flink API tokens — never parsed as
  code or executed.
- EMR checks emit only when EMR evidence exists; no cross-assumption.
- Depends on 159; EMR model (130) is a soft dependency.
