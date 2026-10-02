# Run: Program K wave 1b — Snowflake adapter (spec 213)

- **Initial HEAD**: `777ccc0` (warehouse core)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/213-snowflake-adapter.md`

## Scope

First vendor adapter on top of the generic `WarehouseProjectModel`
(212). Parses Snowflake DDL + Terraform `snowflake_*` + observed
metadata exports; merges into the generic model; SNOW001–005 checks;
`snowflake inspect`; runtime query-history ingestion.

## Files changed

- `src/forge_doctor/analyzers/snowflake_model.py` — **new**:
  `SnowflakeModel` (warehouses, databases, schemas, tables, views,
  stages, pipes, streams, tasks, roles/grants, copies, unparsed export
  shapes). Sources: sqlglot statement index (`command`/`create`/`copy`
  kinds — Snowflake DDL arrives as `Command`), Terraform blocks, and
  `snowflake/` / `.forge-doctor/evidence/` / `information_schema*`
  JSON/CSV exports (tolerant; unknown shapes recorded in
  `unparsed_objects`).
- `src/forge_doctor/analyzers/warehouse_model.py` — merge Snowflake
  facts into the generic model, deduped against Terraform-mapped rows.
- `src/forge_doctor/analyzers/runtime_evidence.py` — Snowflake
  query-history exports → `RuntimeEvidenceModel` queries/errors with
  `EvidenceKind.RUNTIME`.
- `src/forge_doctor/analyzers/platform_graph_builder.py` — vendor
  objects the shared model doesn't carry (stage/stream/pipe/task/
  role/grant) emitted under the `snowflake` warehouse; pipe→table
  `WRITES_TO` via embedded `COPY INTO` (same file+line), stream→table
  `READS_FROM` via `ON TABLE`.
- `src/forge_doctor/checks/snowflake.py` — **new**: SNOW000 surface
  marker (pass/info), SNOW001 no `auto_suspend`, SNOW002 asymmetric
  `auto_resume`, SNOW003 large observed table without clustering,
  SNOW004 `COPY INTO` from public/insecure stage, SNOW005 `SELECT *`
  in persisted view/procedure DDL.
- `src/forge_doctor/cli/snowflake.py` — **new**: `snowflake inspect`.
- `src/forge_doctor/knowledge/capabilities/snowflake.json` — **new**
  pack: time travel, zero-copy clone, Snowpipe, streams/tasks,
  clustering, result caching, multi-cluster warehouses.
- `src/forge_doctor/checks/__init__.py`, `cli/__init__.py`,
  `core/incremental.py` (`snowflake` domains), `docs/checks.md`,
  `README.md`.
- `labs/snowflake/no-auto-suspend/` — positive scenario.
- `labs/snowflake/generic-sql/` — adversarial: non-Snowflake SQL must
  produce zero SNOW findings.
- `tests/unit/test_snowflake.py` — **new**, 17 tests.

## Design decisions

- **Dialect discipline**: generic `sql`-dialect statements never
  attribute to Snowflake; the adversarial lab pins this. Vendor
  attribution needs Snowflake-only syntax, `snowflake_*` Terraform, or
  Snowflake-shaped export paths.
- **`CREATE PIPE ... AS COPY INTO`** parses as one `Command`; the
  embedded copy is extracted by regex into `model.copies` and wires the
  pipe's `WRITES_TO` edge.
- **`ON TABLE` isn't key=value** — extracted explicitly into stream
  attrs for `READS_FROM` edges.
- **SNOW004 conservatism**: undeclared stages can't be verified — only
  provably-public/insecure stage URLs flag.
- **SNOW000** is a pass-severity "no Snowflake evidence" marker so the
  check module reports honestly on silent runs (consistent with other
  families' surface checks).

## Found & fixed en route

- `_ddl_attrs` missed non-`key=val` relation keys — `on_table` now
  extracted explicitly.
- Pipe→copy correlation needed the embedded copy's file+line to match
  the pipe object.

## Tests / gates

- `pytest -k snowflake`: **17 passed**
- `pytest -k "snowflake or warehouse"`: **29 passed**
- `lab run`: **13/13 PASS** (incl. both new scenarios)
- `test_docs.py`: 5 passed
- ruff/mypy on touched files: clean
- `snowflake inspect labs/snowflake/no-auto-suspend`: renders

## Known limitations

- Observed-export shapes are best-effort; unrecognized layouts land in
  `unparsed_objects` for visibility rather than being dropped.
- Query-history parsing covers common export columns; exotic schemas
  degrade to fewer runtime rows.
- Spec remains in `active/` pending human review.
