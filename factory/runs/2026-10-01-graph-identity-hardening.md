# Run: graph identity hardening (spec 181)

Per `prompt_evolucao4.md` — review of HEAD `f244c54`, CI green, no P0.
The reviewer flagged two P1s to land before the Capability Engine and
one P2 pulled forward. Spec written, staged, implemented, verified.

## P1a — canonical table identity

`table:sql:catalog.db.orders` vs `table:iceberg:catalog.db.orders` were
two nodes for the same table. Fix: a catalog-aware resolver derives the
namespace from **observable facts**, not the producing model:

- `_catalog_impls(ctx)` collects `spark.sql.catalog.<name> = <impl>`
  (+ IaC catalog) evidence values.
- `_catalog_domains` claims `name -> iceberg` only when an impl string
  contains `iceberg` - positive proof, never name similarity.
- `_table_id(name, declared, domains)` lets a known catalog qualifier
  override the producer's domain: `SELECT ... FROM prod.sales` with
  `spark.sql.catalog.prod = org.apache.iceberg...` mints
  `table:iceberg:prod.sales` - the *same node* the iceberg adapter mints
  for `CREATE TABLE prod.sales USING iceberg`. Verified end-to-end:
  the SELECT's READS edge lands on the iceberg table entity.
- The iceberg adapter now *drops* table evidence under catalogs with a
  positively non-iceberg impl (e.g. `spark.sql.catalog.hive =
  HiveCatalog`) - previously it claimed `hive.sales` as iceberg.
  Non-iceberg catalogs also mint no `catalog:iceberg:` node and no
  GOVERNS edges.
- Unqualified refs (`SELECT FROM orders`) stay `table:sql:orders` -
  honest non-merge when identity isn't demonstrable.

## P1b — streaming endpoint identity

`StreamingQuery` gained `source_identifier` / `sink_identifier`,
extracted from literal call args only (the AST index drops non-literal
args, so `""` always means *unidentified*):

- `toTable("cat.db.t")` / `start("path")` / `option("topic"|"table"|"path")`
  -> sink identifier.
- `table("cat.db.t")` / `load("s3://...")` (non-format arg) /
  `option("subscribe"|"subscribePattern"|"streamName"|"table"|"path")`
  -> source identifier.

Builder: identified endpoints land on canonical ids
(`stream:kafka:orders`, `table:iceberg:prod.sales` via the resolver);
unidentified ones mint `dataset:<fmt>:<fmt>` with `identified=no` - an
honest marker, never a fake `table:iceberg:iceberg` collapse.
`streaming inspect` renders `kafka(orders) -> iceberg(prod.sales)`.

## P2 — semantic blast-radius

`impact_reachable(graph, id)` in `platform_graph_builder.py` (consumer
layer; `core/platform_graph.py` untouched):

- inbound-only: DEPENDS_ON, READS, CONSUMES, STORED_IN (the edge's src
  depends on the changed target)
- outbound-only: INVOKES, DEFINES, GOVERNS, TRIGGERS (the edge's dst is
  what a changed src affects)
- both ways: WRITES, PRODUCES (changed table hits its writers; changed
  writer hits downstream data)

`platform blast-radius` now uses it: changing `task:airflow:extract`
impacts `task:airflow:load` (dependent); the reverse holds correctly
(false).

## Verification

- `pytest tests/unit/test_platform_graph_population.py` - 19 passed (+7)
- `pytest -x -q` - 708 passed
- `mypy src` - 92 files clean - `ruff check` + `format --check` - clean

## Open questions

- Catalogs registered with no impl evidence (`spark.sql.catalog.x` seen
  only as a ref prefix, IaC `aws_glue_catalog_database`) are *not*
  claimed by `_catalog_domains` - SQL refs under them stay `table:sql:`.
  Joining them needs catalog-type facts (spec 173 capability packs or
  180 Connected Data).
- Delta catalogs (`spark.sql.catalog.x` impl containing `delta`) aren't
  claimed either - no delta model exists to corroborate. Same fix path
  when one lands.
