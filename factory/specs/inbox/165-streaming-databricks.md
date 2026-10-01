---
id: 165-streaming-databricks
title: Streaming stage 7 - Databricks structured streaming, Lakeflow, Auto Loader
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_databricks.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 7 of 10. Databricks recommends
Lakeflow Pipelines (Spark Declarative Pipelines) for new streaming;
production guidance: Jobs (not all-purpose compute), Continuous
scheduling, no autoscale on streaming compute, RocksDB/async state
checkpoint for stateful workloads. Auto Loader = `cloudFiles` format.

# Acceptance Criteria
- Model gains: `lakeflow` evidence (`@dlt.*`/`@dp.*` decorators,
  `dlt.read_stream`/`spark.readStream` in pipeline files, `pipelines`
  bundle resources), `auto_loader` facts (`format("cloudFiles")`,
  `cloudFiles.schemaLocation`, `cloudFiles.schemaEvolutionMode`,
  rescued-data column use), databricks compute/scheduling hints when
  visible in job JSON/bundles.
- New checks: DBXSTR001 manual orchestration candidate for Lakeflow
  (INFO), DBXSTR002 unmanaged checkpoint logic inside a Lakeflow
  pipeline (WARNING), DBXSTR004 obsolete DLT API usage (INFO),
  DBXSTR010 streaming on all-purpose compute evidence (WARNING),
  DBXSTR012 autoscale enabled on streaming job compute (WARNING),
  DBXSTR013 `display()`/`count()` left in production stream path
  (INFO); DBXAL001 `cloudFiles` without schemaLocation (WARNING),
  DBXAL002 checkpoint/schema-location collision (WARNING), DBXAL003
  implicit schema evolution (INFO), DBXAL005 manual file-listing
  discovery where Auto Loader fits (INFO).
- knowledge packs:
  `knowledge/streaming/databricks/{structured-streaming,lakeflow,
  autoloader}.json`.
- Tests + docs + CHANGELOG.

# Constraints
- Compute/schedule evidence only from committed job JSON/bundle files —
  never claims about workspace state.
- Lakeflow recommendations are contextual candidates, not mandates.
- Depends on 159; Databricks model (131) is a soft dependency.
