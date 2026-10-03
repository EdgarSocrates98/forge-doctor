# Run: Program K wave 3 — Trino adapter (spec 218)

- **Initial HEAD**: `c87a868` (data contracts + schema evolution)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/218-trino-adapter.md`

## Scope

Federated/distributed SQL via Trino. Evidence is config-centric:
`etc/catalog/*.properties` (one catalog per file, `connector.name=`
attribution), `config.properties` (coordinator/worker, memory, spill,
resource groups), `node.properties`, `jvm.config`, authored
`catalog.schema.table` SQL refs, and optional observed cluster JSON
exports. Capability pack adds per-connector capability surfaces.

## Files changed

- `src/forge_doctor_data/analyzers/trino_model.py` — **new**:
  `TrinoProjectModel` (catalogs+connectors, coordinator/worker props,
  node props, jvm flags, resource-groups/event-listener file evidence,
  three-part refs, observed rows, unparsed list), deterministic
  `.properties` subset parser (`key=value`, `#`/`!` comments — no JVM).
- `src/forge_doctor_data/checks/trino.py` — **new**: TRINO000 census,
  TRINO001 hive catalog without metastore keys, TRINO002 coordinator
  without spill config while authored SQL writes, TRINO003 test
  connector (`tpch`/`jmx`/`system`/`blackhole`/`memory`/`localfile`/
  `tcds`) in a deployment with data catalogs, TRINO004 multi-catalog
  without resource groups, TRINO005 three-part SQL ref to undeclared
  catalog (MEDIUM confidence).
- `src/forge_doctor_data/cli/trino.py` — **new**: `trino inspect` prints
  catalogs/connectors, cluster role, spill/resource-group state, refs
  (unknown catalogs marked), observed + unparsed files.
- `src/forge_doctor_data/knowledge/capabilities/trino.json` — **new**:
  per-connector surfaces (FEDERATED_READ/WRITE, TRANSACTIONAL_TABLES,
  PUSHDOWN) gated on the `connector` context attribute, plus
  SPILL_TO_DISK / RESOURCE_GROUPS / EVENT_LISTENERS facts.
- `src/forge_doctor_data/analyzers/platform_graph_builder.py` — `_trino`
  adapter: `catalog:trino:<name>` entities (connector attr) `CONTAINS`
  `table:trino:<catalog.schema.table>` for refs whose catalog is
  declared; unknown-catalog refs skipped (TRINO005 reports them).
- `src/forge_doctor_data/checks/__init__.py`,
  `src/forge_doctor_data/cli/__init__.py`,
  `src/forge_doctor_data/core/incremental.py` (`CONFIG | SQL | FILES`),
  `src/forge_doctor_data/core/ontology.py` + `docs/ontology.md`
  (`trino` producer domain) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/trino/prod-cluster/` (TRINO001–005 all fire),
  `labs/trino/plain-props/` (adversarial: generic `.properties` +
  two-part SQL stays silent).
- `tests/unit/test_trino.py` — **new**, 26 tests.

## Design decisions

- **Attribution gate**: `connector.name=` in a catalog-dir
  `.properties`, or `config.properties` carrying
  `coordinator`/`discovery.uri`/`node-scheduler.*` markers (or adjacent
  to catalog evidence). Bare `.properties` files and three-part SQL
  alone never attribute — the adversarial lab pins silence.
- **`node.properties`/`jvm.config` claimed only when adjacent** to
  catalog/config evidence — the filenames are too generic to claim
  alone.
- **TRINO002 "ETL-style" = authored writes** — any sql statement with
  `tables_written` (INSERT/CTAS/MERGE). Deterministic proxy for
  memory-heavy queries.
- **TRINO003 needs a data catalog alongside** — a pure-tpch cluster is
  a benchmark box, not prod-leakage; the check only fires when test
  connectors sit beside data connectors.
- **TRINO005 is MEDIUM confidence** — three-part names occur in
  Snowflake/dbt etc.; the finding describes the gap ("no catalog file
  for this prefix"), not a verdict.
- **Graph skips unknown-catalog refs** instead of fabricating entities
  — the check reports them; graph ids use the declared catalog's real
  casing via a `name.lower() -> id` map.
- **Capability pack gates on `connector`** — base `supported` with
  `conditions` downgrading read-only connectors (tpch/system/jmx/…)
  and non-transactional ones; config-dependent capabilities
  (`spill_configured`, `resource_groups`, `event_listeners`) report
  CONDITIONAL when the attribute is absent.
- **Presto explicitly out of scope** — no `presto` detection branching,
  per the spec's open question.

## Found & fixed en route

- Pack loader requires integer `schema_version: 2` — the initial draft
  used `"2.0"` and silently loaded nothing (entries evaluated UNKNOWN).
- Base `conditional` status can never resolve to `supported` —
  conditions only downgrade; proven-good contexts must start from
  `supported`.
- `catalog_dirs` adjacency is computed from real catalog files, so an
  empty `etc/catalog/` dir doesn't claim `node.properties`.
- Producer-domain audit: all emitted entity domains were already
  covered by `_PRODUCER_DOMAIN_DEFS`; `trino` added. (snowflake/
  bigquery/redshift vendor entities use domain `warehouse` +
  `platform` attr — no missing declarations.)

## Validation

- `pytest tests/unit/test_trino.py` — 26 passed.
- Lab `prod-cluster` — TRINO001, TRINO002, TRINO003, TRINO004,
  TRINO005 all fire; `plain-props` stays silent (TRINO000 PASS only).
- Capability registry — trino pack loads clean (no validation issues);
  connector/flag gating verified (`iceberg` write → SUPPORTED,
  `tpch` write → UNSUPPORTED, absent attrs → CONDITIONAL).
- `forge-doctor-data trino inspect labs/trino/prod-cluster` renders
  catalogs, cluster role, spill/rg state, and marks `bogus.dw.orders`.
- ruff format/check + mypy clean on touched files.

## Open items / boundaries

- Athena/Spark federation not duplicated — existing packs cover it; no
  demonstrated gap (spec boundary honored).
- Presto coordinator semantics deferred to a future spec.
