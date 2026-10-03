# Phase 9 run — Streaming Runtime + Kafka/Kinesis/Flink

- Spec: `factory/specs/active/190-streaming-runtime-intelligence.md`
- Commit message: `feat(streaming): add Kafka Kinesis Flink and runtime stream diagnostics`

## Implemented

- `analyzers/kafka_model.py` — `KafkaProjectModel`: MSK
  provisioned/serverless clusters (transit encryption, client auth,
  brokers, public access, logging), topics (partitions/replication/
  config keys), Spark SS kafka options, `KafkaConsumer`/`KafkaProducer`/
  `AdminClient`/`SchemaRegistryClient` calls, consumer groups
  (`group.id`/`group_id`), schema-registry + TLS/SASL flags. TF + CFN.
- `analyzers/kinesis_model.py` — `KinesisProjectModel`: streams
  (shards, mode, retention, encryption, tf_label), EFO consumers,
  Firehose, KinesisAnalyticsV2 managed-Flink apps, SS kinesis options,
  boto3 `kinesis` calls with StreamName/Consumer literals.
- `analyzers/flink_model.py` — `FlinkProjectModel`: env entries,
  sources, keyed ops/state, windows, timers, checkpoints (interval +
  mode), savepoints, parallelism, sinks, `DeliveryGuarantee`, state
  backends, watermark strategies; managed-app jobs via TF/CFN.
- `core/delivery.py` — `derive_delivery` truth table:
  at-most-once / at-least-once / effectively-once /
  exactly-once-claim / unknown from
  source+checkpoint+engine+sink+idempotency; `per_query` for all SS
  queries. Checkpoint alone never claims exactly-once.
- `analyzers/streaming_runtime.py` — `diagnose_progress` over N
  progress artifacts: SRATE001 (rate imbalance), SSTATE002 (state
  growth), SWM003 (watermark lag >60s), SCKPT004 (commit vs baseline
  3x), SKFK005 (partition backlog), SDUR006 (slow batch >30s).
- Runtime adapters: `flink_checkpoints` (REST checkpoint history,
  `CheckpointFailed` error on failures), `stream_metrics`
  (`MillisBehindLatest`/`records-lag`/consumer-lag metric exports).
- `checks/streaming_bus.py` — KFK000-006, KIN000-003, FLK000-004,
  STREAM080 (per-query delivery semantics with basis evidence).
- `cli/streaming_bus.py` — `kafka|kinesis|flink inspect|findings`;
  `streaming diagnose <files>` + `streaming semantics` in
  `cli/streaming.py`.
- Graph `_streaming_bus` adapter + `_TF_TYPED` additions:
  `stream:kafka:*`, `stream:kinesis:*`, `stream:firehose:*`,
  `compute_job:flink:*`, `principal:kafka:group:*`,
  `principal:kinesis:*`; EFO consumer → stream `CONSUMES` edges
  resolved via TF label → stream name.
- Packs: `streaming/delivery`, `kafka/config`, `kinesis/config`,
  `flink/config`, `capabilities/{kafka,kinesis,flink}`.

## Key decisions

- Delivery semantics are *derived claims with a basis tuple*, never a
  flag lookup: no checkpoint → at-most-once; checkpoint + replayable
  source + transactional/idempotent sink → effectively-once;
  Flink `EXACTLY_ONCE` mode + transactional sink →
  `exactly-once-claim` (certainty `claimed`, not `derived`); missing
  source/sink → `unknown` certainty.
- `SCKPT004` compares commit time to the *fastest* batch (baseline),
  not the median — sustained slowdowns still surface.
- `KafkaClientCall` requires exact API-name match — `NotKafkaConsumer`
  and similar substrings are not evidence.
- Flink file gate requires a flink-ish token (`flink`,
  `StreamExecutionEnvironment`, `enable_checkpointing`,
  `addSource(`/…) — stray `key_by` fragments in arbitrary files are
  not jobs.
- Consumer groups / EFO consumers are PRINCIPAL entities (identity
  that consumes); topics/streams are STREAM entities. SS endpoint
  entities converge by canonical id with TF-declared resources.

## Verification

- `pytest tests/unit/test_streaming_bus_models.py
  test_streaming_bus_checks.py test_streaming_runtime.py
  adversarial/test_streaming_bus.py -q` → 62 passed
- `pytest -q` → 1172 passed, 0 failed
- `mypy src` → clean (138 files)
- `ruff check src tests` + `ruff format` → clean
- `forge_doctor_data knowledge verify` → all packs ok (74)
- CLI smoke: `kafka|kinesis|flink inspect`, `streaming diagnose`,
  `streaming semantics`, `platform graph` on a multi-bus fixture —
  entities converge (`stream:kafka:orders` from TF + SS), EFO→stream
  edge resolves.
- One post-commit fix folded in: capability `versions` map must use
  valid statuses (removed `{"default": "24h"}` from
  `KINESIS_EXTENDED_RETENTION`).

## Open questions

- Whether `principal:kafka:group:<g>` should remain PRINCIPAL or get a
  dedicated consumer kind — flagged for review; no blocking ambiguity.
