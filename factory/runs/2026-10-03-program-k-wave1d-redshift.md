# Run: Program K wave 1d — Redshift adapter (spec 215)

- **Initial HEAD**: `3ba300e` (BigQuery adapter)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/215-redshift-adapter.md`

## Scope

Third vendor adapter on `WarehouseProjectModel` (212). Parses Redshift
DDL + Terraform `aws_redshift*` + observed `SVV_*`/`STL_*` exports;
merges into the generic model; RS001–005 checks; `redshift inspect`;
STL query-log runtime adapter.

## Files changed

- `src/forge_doctor/analyzers/redshift_model.py` — **new**:
  `RedshiftProjectModel` (clusters/workgroups, databases/schemas incl.
  Spectrum external schemas, tables with diststyle/distkey/sortkey/
  encode attrs, materialized views, datashares, maintenance queries,
  observed SVV/STL rows, `ato_available()` gate).
- `src/forge_doctor/analyzers/warehouse_model.py` — `_from_redshift`
  merge: compute, namespaces (external_schema→schema), tables, views,
  queries; parameter groups→workload_management; datashares→sharing.
- `src/forge_doctor/analyzers/runtime_evidence.py` —
  `RedshiftQueryLogAdapter`: STL_QUERY-style exports → runtime model
  (executions + execution_time + wlm_queue_time + aborted→error).
- `src/forge_doctor/analyzers/platform_graph_builder.py` — Redshift
  vendor objects (datashare→catalog, authz/consumer/iam→principal,
  routine→compute_job, the rest→infrastructure_resource).
- `src/forge_doctor/checks/redshift.py` — **new**: RS000 census,
  RS001 large EVEN/ALL+joined table (observed_metadata evidence),
  RS002 unsorted table behind range predicates, RS003 ATO disabled with
  skew, RS004 public/unencrypted cluster, RS005 manual VACUUM/ANALYZE
  gated on ATO-eligible compute.
- `src/forge_doctor/cli/redshift.py` — **new**: `redshift inspect`.
- `src/forge_doctor/knowledge/capabilities/redshift.json` — **new**
  pack: Spectrum, datashares, RA3 managed storage (conditional on
  node_type), Serverless RPU, concurrency scaling, auto MVs/ATO
  (conditional), dist/sort keys.
- Registrations, `docs/checks.md`, `README.md`.
- `labs/redshift/public-cluster/` (RS004), `labs/redshift/skewed-even/`
  (RS001), `labs/redshift/postgres/` (adversarial).
- `tests/unit/test_redshift.py` — **new**, 19 tests.

## Design decisions

- **Exclusive markers only**: `DISTSTYLE|DISTKEY|SORTKEY|INTERLEAVED|
  ENCODE|SVV_*|STL_*|STV_*|SVL_*|CREATE EXTERNAL SCHEMA|TABLE|
  DATASHARE|UNLOAD TO|IAM_ROLE|SPECTRUM`. `VACUUM`/`ANALYZE` are NOT
  markers — Postgres shares them; the adversarial lab proves silence.
- **RS001 join evidence** per the spec open question: either authored
  SQL joins or observed `STL_QUERY` text count; the finding names which
  (confidence split) and always marks `observed_metadata` since the
  size/skew facts are export-derived.
- **RS003/RS004 mark `config`** — Terraform facts; **STL/SVV-derived
  facts mark `observed_metadata`** per the spec constraint.
- **RS005 gates on `ato_available()`** — ra3+/dc2+/ds2 node types or a
  serverless workgroup. No compute evidence → no finding (honest
  unknown, not assumed availability).
- **`auto_analyze`/`automatic_table_optimization`** are mined from
  parameter-group `parameter{}` blocks and cluster attrs — both real
  surfaces; absence is not flagged.

## Found & fixed en route

- `\b(...)\b` around symbolic operators (`>=`) never matches — the
  word-boundary requires a word char. Dropped `\b` from the operator
  alternation in the RS002 range-predicate regex.
- Terraform bool attrs arrive as Python `True`/`False` — comparisons
  normalize with `.lower()`.

## Tests / gates

- `pytest -k redshift`: **19 passed**
- `lab run`: **18/18 PASS** (incl. `public-cluster`, `skewed-even`,
  adversarial `postgres`)
- `test_docs.py`: 5 passed
- ruff/mypy on touched files: clean
- `redshift inspect labs/redshift/public-cluster`: renders

## Known limitations

- `parameter{}` mining is regex-on-body — exotic HCL (dynamic blocks)
  may under-report WLM config.
- `STL_QUERY` text fields aren't re-parsed for table refs — join/range
  matching on observed queries is substring/tail-name based (documented
  heuristic, lower confidence than authored SQL).
- Spec remains in `active/` pending human review.
