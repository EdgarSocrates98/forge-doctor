# Run: connected-data phase B2 — graph population + platform CLI (spec 172)

## What shipped

- `analyzers/platform_graph_builder.py` — `build_platform_graph(ctx)`
  memoized on ctx; seven unidirectional adapters, one per domain model.
  Models never import each other — the builder is the single composition
  point.
  - **airflow**: DAG → `workflow:airflow`, tasks → `task:airflow`
    (INVOKES), `a >> b` edges → task DEPENDS_ON (resolved through a
    var/task_id alias map — edges name vars, entities key task_ids).
  - **control-m**: folders → `workflow:controlm` (INVOKES jobs), jobs →
    `task:controlm:{folder}.{job}`; producer `add_events` × consumer
    `wait_events` joined on identical event names → DEPENDS_ON
    (`DERIVED` — the edge exists only because two facts agree).
  - **stepfunctions**: machine → `workflow:stepfunctions`, states →
    `task:stepfunctions:{machine}.{state}` (INVOKES); Task `Resource`
    ARNs → service entity (lambda→compute_job, glue→compute_job,
    athena→query, dynamodb→table, sns/sqs→stream; `sdk:*` →
    infrastructure_resource). `arn:aws:states:::*` pattern resources mint
    nothing — they name the integration, not an instance.
  - **streaming**: query → `stream:spark_ss:{file}:{line}:{name}`;
    source→CONSUMES, sink→PRODUCES; endpoint kind by format class
    (kafka/kinesis→stream, delta/iceberg→table, else dataset).
  - **sql**: statement → `query:sql:{file}:{line}`; tables_read→READS,
    tables_written→WRITES (`STATIC`).
  - **iceberg**: tables→`table:iceberg`, catalog names→`catalog:iceberg`,
    `catalog.X`-prefixed tables→GOVERNS (`DERIVED` prefix join).
  - **parquet**: on-disk `file` evidence→`dataset:parquet:{path}` +
    `storage_location:parquet:{dir}` + STORED_IN
    (`OBSERVED_METADATA`). Reader/writer call-sites mint nothing —
    `value` is a dotted call, not a location.
  - **terraform**: every resource→`infrastructure_resource:aws:{addr}`;
    typed resources (sfn/lambda/glue/dynamodb/s3/kinesis/neptune/glue-db)
    also emit their platform entity + DEFINES (`CONFIG`).
- `cli/platform.py` — `platform graph` (entity/rel census, `--json`
  deterministic export) + `platform blast-radius <substr>` (matching
  entities → cycle-safe reachability). Registered in `cli/__init__.py`.

## Canonical id policy

Entity `domain` is the *platform* namespace (`table:dynamodb:x`,
`workflow:stepfunctions:m`, `compute_job:lambda:f`), not the producing
model — so a Terraform `aws_dynamodb_table` and future DynamoDB code
evidence merge on the same id. SFN machines key on the TF label (that's
the name `stepfunctions_model` assigns to embedded definitions), letting
`infrastructure_resource:aws:aws_sfn_state_machine.pipe` DEFINES-edge
land on the machine the ASL adapter produced — verified by
`test_cross_domain_join_shared_id` (infra → workflow → state-task →
`compute_job:lambda:fn` chain).

## Constraints honored

- Deterministic joins only — every edge needs observable identity (same
  name/ARN/label/event name); no similarity matching.
- Absence of a model → adapter no-ops (each model degrades on empty).
- No capability inference — attrs describe what exists; `supports()`
  stays with spec 173.

## Verification

- `pytest tests/unit/test_platform_graph_population.py` — 12 passed
- `pytest -x -q` — 701 passed (+12)
- `mypy src` — 92 files clean · `ruff check` + `format --check` — clean

## Open questions

- Glue has no project model (checker-level `glue_ast.py` only) — no
  adapter. If a `GlueProjectModel` lands later, it joins via
  `compute_job:glue:` automatically.
- SQL tables key on `table:sql:` (statement-plane); joining them to
  `table:iceberg:` needs catalog-qualification knowledge the SQL model
  doesn't carry — deferred to Connected Data (spec 180).
