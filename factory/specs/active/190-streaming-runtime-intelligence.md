---
id: 190
title: Streaming Runtime + Kafka/Kinesis/Flink Deep Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_streaming_bus_models.py tests/unit/test_streaming_bus_checks.py tests/unit/test_streaming_runtime.py tests/unit/adversarial/test_streaming_bus.py -x -q
  - python -m forge_doctor kafka inspect <fixture>
  - python -m forge_doctor streaming diagnose <progress-files>
  - python -m forge_doctor knowledge verify
---

# Phase 9 - Streaming Runtime + Kafka/Kinesis/Flink Deep Intelligence

## Context

Phase 9 of the ten-phase Forge Doctor program: complete streaming-bus
and runtime-stream reasoning. Existing surface: `streaming_model.py`
(Spark SS queries with source/sink/checkpoint/watermark/stateful ops),
`spark_ss_progress` runtime adapter, STREAM001-070 checks. No Kafka,
Kinesis, or Flink project models; no runtime streaming diagnostics;
no formalized delivery semantics.

## Acceptance Criteria

- `KafkaProjectModel`: MSK provisioned/serverless clusters
  (encryption-in-transit, client auth, broker nodes/instance, public
  access, logging), topics (partitions, replication, config keys),
  Spark SS kafka options (`subscribe`, `assign`, `startingOffsets`,
  `maxOffsetsPerTrigger`, `minOffsetsPerTrigger`, `failOnDataLoss`,
  `kafka.group.id`, deserializers, `security.protocol`/`sasl`), Python
  clients (`KafkaConsumer`, `KafkaProducer`, `AdminClient`,
  `SchemaRegistryClient`), consumer groups, schema-registry presence.
  Terraform + CloudFormation + code.
- `KinesisProjectModel`: streams (shard_count, stream_mode,
  retention_period, encryption), EFO consumers
  (`aws_kinesis_stream_consumer`, `subscribe_to_shard`,
  `register_stream_consumer`), Firehose delivery streams,
  KinesisAnalyticsV2 managed-Flink apps, SS kinesis options, boto3
  `kinesis` calls with StreamName/Consumer literals.
- `FlinkProjectModel`: jobs (StreamExecutionEnvironment entrypoints +
  managed apps), sources, keyed state, windows, timers, checkpointing
  (`enableCheckpointing`, interval, `CheckpointingMode`), savepoints,
  parallelism, sinks, `DeliveryGuarantee`, state backends, watermark
  strategies.
- `derive_delivery` formalizes delivery semantics:
  `at-most-once` / `at-least-once` / `effectively-once` /
  `exactly-once-claim` / `unknown`, derived deterministically from
  source + checkpoint + engine + sink + retry/idempotency evidence.
  A checkpoint alone never produces an exactly-once claim; missing
  evidence yields `unknown`-certainty output.
- `diagnose_progress` runtime diagnostics over a
  `StreamingQueryProgress` batch series: SRATE001 rate imbalance,
  SSTATE002 state growth, SWM003 watermark lag, SCKPT004
  walCommit/commit instability, SKFK005 source offset backlog,
  SDUR006 slow batches.
- Runtime adapters: `flink_checkpoints` (checkpoint history, failed
  checkpoints as errors) and `stream_metrics` (`MillisBehindLatest`,
  `records-lag`, consumer lag exports).
- Checks KFK000-006, KIN000-003, FLK000-004, STREAM080 (delivery
  semantics per query); deterministic; absent evidence never becomes
  a negative claim.
- Platform graph: `stream:kafka:*`/`stream:kinesis:*`/
  `stream:firehose:*`/`compute_job:flink:*`/`principal:kafka:group:*`/
  `principal:kinesis:*` entities, EFO-consumer→stream CONSUMES edges;
  TF topic/stream entities converge with SS endpoint entities by
  canonical id.
- Knowledge packs: `streaming/delivery`, `kafka/config`,
  `kinesis/config`, `flink/config`, `capabilities/{kafka,kinesis,flink}`.
- CLI: `kafka|kinesis|flink inspect|findings`, `streaming diagnose`,
  `streaming semantics`.
- Unit + adversarial tests: empty projects, substring-name FPs,
  partial progress files, missing evidence → unknown, determinism.

## Constraints

- Deterministic, offline-first; no cloud calls, no target-code
  execution, no mandatory LLM.
- Delivery claims must stay explainable (basis tuple on every result).
- Non-replayable sources cap derived semantics; spoofed identifiers
  never fabricate cross-domain entities.

## Review Notes

- Verify the `derive_delivery` truth table covers the spec's four
  levels plus `unknown` and never upgrades a claim on checkpoint
  presence alone.
- Verify `SCKPT004` compares against the baseline (fastest) commit,
  not the median, so sustained slowdowns still surface.
- Confirm graph convergence: TF `aws_msk_topic "orders"` and SS
  `subscribe: orders` must land on the same `stream:kafka:orders` id.
