# Run: 159-streaming-model (Streaming stage 1)

- Spec: `factory/specs/active/159-streaming-model.md` (staged, grill: completed)
- Agent: devin
- Source prompt: `prompt_evo_streaming.md` — 10-subcycle Streaming
  Intelligence program (generic model → Spark SS → checkpoint/state/
  watermark → Kafka/Kinesis → Glue RTM → EMR/Flink → Databricks/Lakeflow
  → CDC/schema registry → runtime metrics → cross-platform graph).

## Implemented

- `StreamingProjectModel` (`analyzers/streaming_model.py`) —
  platform-agnostic; stage-1 producer is Spark Structured Streaming via
  the AST index. `StreamingQuery` exposes file/line/name (queryName or
  write-receiver), engine (`spark_ss`), source/sink format classes,
  output_mode, trigger kind+arg, checkpoint (literal or `dynamic`
  composition flag), watermark `col:delay`, stateful op set,
  foreachBatch presence, grouping strategy (`receiver`|`file`).
- Query reconstruction: stream vars discovered via `x = <readStream>`,
  propagated through a fixpoint over assigns (`agg = df.groupBy(…)`
  inherits streamness), union-merged so read/write halves of one
  logical query land on one record; `spark.readStream...` chains are
  attributed to the nearest preceding stream assign; unresolvable
  chains fall back to file-level grouping (recorded on the record).
- 7 checks (`category = "streaming"`): STREAM001 anchor, STREAM002
  missing checkpointLocation (WARNING), STREAM003 temp-path checkpoint
  (WARNING), STREAM013 shared checkpoint across queries (WARNING),
  STREAM014 dynamic checkpoint (WARNING), STREAM020 stateful-without-
  watermark honoring the pack's `needs_watermark` op table (INFO),
  STREAM070 foreachBatch detected (INFO).
- `forge-doctor streaming inspect` — per-query `source → sink`,
  mode/trigger/checkpoint/watermark/stateful facts, severity-sorted
  risks. Verified live.
- `knowledge/streaming/spark/stateful_ops.json` — op→state-type map +
  `needs_watermark` flags, schema 2 + sources.
- Inbox specs for sub-cycles 2–10: 160 Spark SS depth (triggers/joins/
  state store), 161 checkpoint-compat fingerprint + watermark/state
  doctors, 162 Kafka/MSK/Kinesis, 163 Glue streaming + RTM, 164 EMR +
  Flink, 165 Databricks + Lakeflow + Auto Loader, 166 CDC + schema
  registry, 167 runtime progress/lag/throughput/cost, 168 cross-domain
  graph + migrate/modernize + reliability scorecard.

## Latent quirks found

- The AST index never emits a bare `readstream`/`writestream` call —
  they exist only as segments inside `dotted` (`load(spark.readStream.
  format)`). Detection must scan `dotted`, not `name`.
- foreachBatch handler names are `Name` nodes — dropped from `args`
  (string-literals only) and never inlined into `dotted`; the model
  honestly records `present`.
- Literal `/tmp/...` checkpoints are *temp-path* evidence (STREAM003),
  not dynamic — `tmp` had to be removed from the dynamic hint regex to
  avoid double-flagging.
- Read-side and write-side chains of one query need union-find merging
  over assign lineage or they surface as two half-empty records.

## Verification

- `pytest -q` (targeted): 17 passed
- `pytest -x -q`: 627 passed (+17)
- `ruff check`, `ruff format --check`: clean
- `mypy src`: clean (89 files)
- E2E: `streaming inspect` on a Kafka→Delta + rate→foreachBatch fixture
  renders merged queries and fires STREAM003/013/001/070.

## Gate

Spec left in `active/` — awaiting human review + `archive --accepted`.
