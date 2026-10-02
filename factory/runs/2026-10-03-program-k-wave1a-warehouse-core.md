# Run: Program K wave 1a — Warehouse Project Model (spec 212)

- **Initial HEAD**: `d37f586` (plus `cf0e4cf` misc restore)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/212-warehouse-model-core.md`

## Scope

Vendor-neutral warehouse semantic core — the foundation the Snowflake /
BigQuery / Redshift adapters (213-215) populate. Generic first, vendors
second (explicit roadmap constraint).

## Files changed

- `src/forge_doctor/analyzers/warehouse_model.py` — **new**:
  `WarehouseProjectModel` (platforms, compute, namespaces, tables,
  views, queries, workload_management/security/sharing/costs surfaces).
  Terraform resource-kind normalization (`snowflake_*`,
  `google_bigquery_*`, `aws_redshift*`) + sqlglot DDL (`CREATE
  TABLE/VIEW/SCHEMA/DATABASE`, external/materialized variants).
  Empty model on non-warehouse projects — no false positives.
- `src/forge_doctor/core/platform_graph.py` — new `EntityKind`
  (`warehouse`, `warehouse_compute`, `view`, `schema`) + `RelKind`
  (`CONTAINS`, `READS_FROM`, `WRITES_TO`).
- `src/forge_doctor/core/ontology.py` + `docs/ontology.md` — term
  definitions + `warehouse` producer domain (doc-sync test enforced).
- `src/forge_doctor/analyzers/platform_graph_builder.py` —
  `_warehouse` adapter: warehouse→compute/namespace `CONTAINS`,
  schema→table `CONTAINS` (dotted names nest), view/query
  `READS_FROM`/`WRITES_TO` to matched tables. Impact policy: CONTAINS
  outbound-only, READS_FROM inbound-only, WRITES_TO both (like WRITES).
- `src/forge_doctor/knowledge/capabilities/warehouse.json` — **new**
  pack: `SQL_QUERY` supported (definitional); `MATERIALIZED_VIEWS`,
  `TIME_TRAVEL`, `RESULT_CACHE`, `SHARING` honestly `unknown` pending
  vendor packs.
- `src/forge_doctor/checks/warehouse.py` — **new**: WARE001/010/020/030.
- `src/forge_doctor/checks/__init__.py`, `core/incremental.py`
  (`warehouse: {TERRAFORM, SQL}`), `docs/checks.md`, `README`.
- `labs/warehouse/redshift-cluster/` — **new** scenario + ground truth.
- `tests/unit/test_warehouse.py` — **new**, 12 tests.

## Design decisions

- **Generic-dialect SQL stays in the `sql` domain** — the warehouse
  adapter only emits entities for vendor-attributed rows
  (`platform != "sql"`), so one producer owns each entity id; no
  double-counting between the `sql` and `warehouse` adapters.
- **Vendor detection is conservative**: terraform resource kinds are
  definitive; SQL rows only attribute a platform when sqlglot parsed a
  warehouse dialect, and only DDL marks `platforms` (SELECT dialect
  guesses are weak evidence).
- **Terraform `name` attr beats the logical label** — declared object
  names (`DB.S.T`) are the semantic identity.
- **workload_management/security/sharing/costs exist but are thin** —
  Terraform produces workload/security rows; SQL can't. Vendor adapters
  fill the rest.

## Found & fixed en route

- `752964d` (Program D) had silently dropped 7 CLI commands
  (`sbom|mcp|lsp|doctor|version|diagnose|trace`) via a truncated
  heredoc — restored verbatim in `cf0e4cf`; `test_version_command`
  caught it on the suite rerun.

## Tests / gates

- `pytest -k warehouse`: **12 passed**
- `lab run redshift-cluster`: **PASS** (WARE001 + WARE030 expected)
- `test_docs.py` + `test_ontology.py`: 18 passed
- `tests/integration/test_cli.py`: 47 passed (post-restore)
- ruff/mypy on touched files: clean
- `ontology validate` clean on warehouse fixtures

## Known limitations

- No vendor semantics — WLM/security/sharing rows are coarse.
- `costs` unpopulated (spec open question: observed-metadata only
  until a stale-pricing policy exists).
- Spec remains in `active/` pending human review.
