---
id: 168-streaming-graph-migration
title: Streaming stage 10 - cross-domain stream graph, migrate/modernize, scorecard
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_graph.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 10 of 10 — the capstone. One graph:
`MSK:orders → Glue Streaming 6.0 → Spark SS → foreachBatch → MERGE →
Iceberg → Athena`. Orchestrators (Airflow/Control-M/Step Functions) and
Terraform streaming resources (`aws_msk_cluster`, `aws_kinesis_stream`,
`aws_glue_job`, `aws_emrserverless_application`, `databricks_pipeline`)
join as edges.

# Acceptance Criteria
- `forge-doctor-data streaming graph .` renders source→engine→sink chains
  with orchestrator/infra edges; deterministic ordering.
- `StreamingReliability` scorecard (factual, not a score): recovery
  (checkpoint/stable location/source replay), state (watermark/
  bounded/timeout), sink (transactional/idempotent), ops (lag
  monitoring/backpressure). Cells = ✓/?/✗ from model evidence.
- `forge-doctor-data streaming migrate .` candidates: DStream → Structured
  Streaming, Spark batch → streaming, Glue 5 streaming → Glue 6 RTM,
  manual Databricks stream → Lakeflow, stream-static → CDC pipeline.
  Report-only; no code rewriting.
- `forge-doctor-data streaming modernize .` candidates: Lakeflow, Glue RTM,
  Flink, Auto Loader, AUTO CDC, event-driven ingestion — contextual.
- Orchestrator checks: AIRSTR001 scheduler repeatedly starts continuous
  stream (WARNING), CTMSTR001 cyclic Control-M over always-on workload
  (WARNING) — emitted only when both orchestrator + stream evidence
  co-exist.
- knowledge pack: `knowledge/streaming/compatibility.json` — engine /
  source / sink capability matrix incl. STREAM_EFFECTIVELY_ONCE
  requirements (recoverable offsets + durable checkpoint +
  deterministic replay + idempotent/transactional sink).
- Tests + docs + CHANGELOG.

# Constraints
- Graph edges require evidence at both ends — no inferred links.
- Effectively-once is a capability check, never a promise; wording
  says "at-least-once side effects" when sink idempotency is unknown.
- Depends on 159–167; orchestrator models (116/122) + terraform model
  (134) are soft dependencies — degrade gracefully.
