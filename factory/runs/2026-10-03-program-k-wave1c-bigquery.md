# Run: Program K wave 1c — BigQuery adapter (spec 214)

- **Initial HEAD**: `fcf7101` (Snowflake adapter)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/214-bigquery-adapter.md`

## Scope

Second vendor adapter on `WarehouseProjectModel` (212). Parses BigQuery
DDL + Terraform `google_bigquery_*`/`google_biglake_*` + observed
`INFORMATION_SCHEMA` exports (tables, partitions, jobs-by-project);
merges into the generic model; BQ001–005 checks; `bigquery inspect`;
jobs-history runtime adapter.

## Files changed

- `src/forge_doctor_data/analyzers/bigquery_model.py` — **new**:
  `BigQueryProjectModel` (datasets, tables w/ partition/cluster attrs,
  views + materialized views, reservations/capacity/assignments/BI
  Engine, connections, routines, jobs, transfers, dataset access /
  authorized views, BigLake catalogs). Sources: sqlglot index
  (`command`/`create` kinds — OPTIONS/PARTITION BY DDL arrives as
  `Command` under the project's spark/generic parse), Terraform blocks
  (nested `time_partitioning`/`clustering`/`access.view` mined from the
  block body), and `bigquery/` / `.forge-doctor-data/evidence/` /
  `information_schema*`/`jobs*` exports.
- `src/forge_doctor_data/analyzers/warehouse_model.py` — `_from_bigquery`
  merge: datasets→schemas, tables (partition attrs preserved), views,
  reservations/capacity→workload_management, queries, observed tables.
- `src/forge_doctor_data/analyzers/runtime_evidence.py` —
  `BigQueryJobsAdapter`: `INFORMATION_SCHEMA.JOBS` exports →
  `RuntimeEvidenceModel` (executions + bytes_processed/bytes_billed/
  slot_ms metrics + error_result).
- `src/forge_doctor_data/analyzers/platform_graph_builder.py` — BigQuery
  vendor objects emitted (reservation/capacity→warehouse_compute,
  connection→infrastructure_resource, routine→compute_job, job→query,
  transfer→task, dataset_access→principal, data_exchange/listing/
  biglake→catalog/database).
- `src/forge_doctor_data/checks/bigquery.py` — **new**: BQ000 census anchor,
  BQ001 large unpartitioned observed table, BQ002 partitioned table
  queried without partition filter (authored=warning, observed
  jobs=error per spec default), BQ003 `SELECT *` cost risk, BQ004
  public access / undocumented authorized view, BQ005 materialized view
  over mutable base without `max_staleness`.
- `src/forge_doctor_data/cli/bigquery.py` — **new**: `bigquery inspect`.
- `src/forge_doctor_data/knowledge/capabilities/bigquery.json` — **new**
  pack: partitioning, clustering, BI Engine, slots/reservations, time
  travel, BigLake, DML quotas (conditional on workload type).
- `src/forge_doctor_data/checks/__init__.py`, `cli/__init__.py`,
  `core/incremental.py` (`bigquery` domains), `docs/checks.md`,
  `README.md`.
- `labs/bigquery/unpartitioned/` — positive (BQ001+BQ002+BQ003).
- `labs/bigquery/plain-sql/` — adversarial.
- `tests/unit/test_bigquery.py` — **new**, 20 tests.

## Design decisions

- **Vendor markers are exclusive-only**: `PARTITION BY`, `OPTIONS(...)`,
  backtick-qualified `p.d.t` refs, `_PARTITIONTIME/_PARTITIONDATE`,
  `CREATE RESERVATION|CAPACITY`, `WITH CONNECTION`, `NOT ENFORCED`,
  `biglake`. `CLUSTER BY` is deliberately *not* a marker — Snowflake
  uses it too; bare-cluster files stay honestly unclaimed.
- **Shared evidence dirs need a field signal**: `.forge-doctor-data/
  evidence/` rows claim for BigQuery only when fields intersect the BQ
  key set (`project_id`, `dataset_id`, `size_bytes`, `partition_id`,
  `total_bytes_*`, `job_id`, `statement_type`, `creation_time`, ...) —
  the Snowflake loader keeps its existing claim rules. Unknown shapes
  land in `unparsed`.
- **`CREATE ...` modifier list excludes `external`/`materialized`** —
  they're part of multi-word kinds (`EXTERNAL TABLE`, `MATERIALIZED
  VIEW`); greedy modifier consumption swallowed them (Snowflake's regex
  already encoded this lesson).
- **BQ002 severity split** per the spec default: authored SQL warns,
  observed job history errors.
- **Name priority**: `name` > `table_id` > `dataset_id` — a table's
  identity is `table_id`, not its parent dataset.

## Found & fixed en route

- `_CREATE_RE` modifier group swallowed `materialized`/`external` —
  MV/external kinds classified as plain view/table.
- `total_bytes_processed`/`job_id` missing from the field signal —
  jobs exports outside `bigquery/` went unclaimed.
- `re.findall` returns `list[Any]` in typeshed — `str()` wrap for mypy.

## Tests / gates

- `pytest -k bigquery`: **20 passed**
- `lab run`: **15/15 PASS** (incl. `unpartitioned` BQ001/002/003 and
  adversarial `plain-sql` silence)
- `test_docs.py`: 5 passed
- ruff/mypy on touched files: clean
- `bigquery inspect labs/bigquery/unpartitioned`: renders
  datasets/relations/queries/exports

## Known limitations

- Nested-block attr mining is regex-on-body; deeply exotic HCL
  (for_each blocks, dynamic blocks) may under-report partition/cluster
  fields — honest absence over guessing.
- `NOT ENFORCED` marks the file; the constraint itself isn't modeled.
- Spec remains in `active/` pending human review.
