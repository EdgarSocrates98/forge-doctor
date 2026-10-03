---
id: 162-streaming-kafka-kinesis
title: Streaming stage 4 - Kafka/MSK + Kinesis source models and checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_kafka.py tests/unit/test_streaming_sources.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 4 of 10. Source-side intelligence:
`option("kafka.bootstrap.servers", …)`/`option("subscribe", …)`/
`option("kinesis.streamName", …)` etc. are string-literal kwargs the
AST index already captures.

# Acceptance Criteria
- `KafkaStreamingModel` + `KinesisStreamingModel` facts attached to the
  streaming model's source records: bootstrap servers, topics
  (subscribe/subscribePattern/assign), startingOffsets,
  failOnDataLoss, maxOffsetsPerTrigger, kafka.security.*, consumer
  group evidence; Kinesis streamName, startingPosition
  (TRIM_HORIZON/LATEST/AT_TIMESTAMP), region, enhanced-fanout hints,
  retention hints.
- New checks: KAFKA001 source anchor, KAFKA002 `startingOffsets`
  earliest on production-shaped job (INFO), KAFKA003 consumer-group
  collision risk across queries (WARNING when same group+topic
  duplicated), KAFKA004 failOnDataLoss explicitly false/disabled
  (INFO), KAFKA005 no throughput bound (`maxOffsetsPerTrigger`/
  `maxRatePerPartition` absent — INFO), KAFKA008 insecure protocol
  (`PLAINTEXT`/no ssl — WARNING); KIN001 shard/consumer parallelism
  hints (INFO), KIN003 retention vs recovery requirement (INFO),
  KIN004 starting position replay risk (INFO); STREAM100 offset
  strategy unclear (INFO), STREAM101 replay may process full history
  (INFO when earliest + no bound).
- CLI: `forge-doctor-data kafka inspect` + `forge-doctor-data kinesis inspect`.
- knowledge packs: `knowledge/streaming/kafka/options.json`,
  `kinesis/options.json`.
- Tests + docs + CHANGELOG.

# Constraints
- Broker/topic names are evidence — never emit secrets/connection
  strings containing credentials in findings (redact userinfo).
- "Production-shaped" = project has deploy/CI evidence; else INFO only.
- Depends on 159.
