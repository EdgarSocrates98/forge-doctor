---
id: 159-streaming-model
title: Streaming stage 1 - StreamingProjectModel, STREAM00# checks, `streaming` CLI
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
`prompt_evo_streaming.md` sub-cycle 1 of 10 (Streaming Intelligence).
Streaming is a transversal execution model — the model must be
platform-agnostic: platform analyzers emit generic streaming facts and
checks consume the shared model. Stage 1 ships the model plus its first
producer (Spark Structured Streaming chains via the AST index; analyzed
code is never executed).

# Acceptance Criteria
- `analyzers/streaming_model.py` `StreamingProjectModel`:
  `StreamingQuery` records (file/line/query_name/engine), source from
  `readStream.format()`/`load()`/options → kafka|kinesis|files|delta|
  iceberg|rate|custom, sink from `writeStream.format()`/`foreachBatch`,
  output_mode, trigger kind + literal arg, checkpoint_location (literal
  + `dynamic` flag when composed from f-string/datetime/uuid/non-literal
  parts), watermark column+delay, stateful ops set (groupBy/window/join/
  dropDuplicates/dropDuplicatesWithinWatermark/mapGroupsWithState/
  flatMapGroupsWithState/transformWithState), foreachBatch handler name.
- `checks/streaming.py` (`category = "streaming"`): STREAM001 anchor
  (queries/sources/sinks counts), STREAM002 writeStream without
  checkpointLocation (WARNING), STREAM003 checkpoint under temp/volatile
  path (WARNING), STREAM013 multiple queries sharing one literal
  checkpointLocation (WARNING), STREAM014 checkpointLocation built
  dynamically (WARNING), STREAM020 stateful ops without `withWatermark`
  (INFO), STREAM070 foreachBatch detected (INFO — idempotency unknown
  statically). why/when_ok/fix each.
- `cli/streaming.py`: `forge-doctor streaming inspect [path]` — queries
  (source → sink), output mode/trigger/checkpoint/watermark/stateful
  ops, severity-sorted risks.
- `knowledge/streaming/spark/stateful_ops.json` — op→state-type map +
  notes, schema 2 + sources.
- Tests per check + CLI; docs checks.md STREAM rows + CHANGELOG.

# Constraints
- Query grouping: one record per `writeStream` receiver/`queryName`
  where statically possible; file-level aggregation is the documented
  fallback — record which strategy applied.
- "Dynamic checkpoint" = no plain string literal (f-string pieces,
  `datetime`, `uuid`, `os.environ`, concatenation).
- Absence findings are evidence-gated: "no checkpointLocation observed
  statically", not "job has no checkpoint".
- No new dependencies; analyzed code never imported/executed.
