# Run: Program K wave 3b — Analytical engines (spec 219)

- **Initial HEAD**: `f8708cc` (Trino adapter)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/219-analytical-engines.md`

## Scope

Shared `AnalyticalEngineModel` for the real-time OLAP family —
ClickHouse, Pinot, Druid — each a thin evidence adapter over a common
row bag. Per-engine check families (`CH###`/`PIN###`/`DRU###`) plus a
shared adversarial lab proving plain JSON/SQL never attributes.

## Files changed

- `src/forge_doctor_data/analyzers/analytical_model.py` — **new**:
  `AnalyticalEngineModel` (`EngineTable`/`PinotSchema`/`ObservedRow`),
  ClickHouse scanner (sql-index `create` statements + engine-family
  allowlist + ORDER BY/PARTITION BY/SETTINGS regex extraction),
  Pinot scanner (`tableName`+`tableType`/`segmentsConfig` table
  configs, `schemaName`+`*FieldSpecs` schemas, flattened
  retention/index props), Druid scanner (`ingestionSpec`/`spec` +
  `dataSchema`), observed-export scanner (dir- or signal-attributed),
  keeper-file discovery (filename or XML content), `has_dedupe_plan`.
- `src/forge_doctor_data/checks/analytical.py` — **new**: CH000 census,
  CH001 MergeTree w/o ORDER BY, CH002 Replicated* w/o keeper config,
  CH003 Distributed w/o local shard, CH004 Kafka w/o dedupe plan;
  PIN000 census, PIN001 realtime w/o retention, PIN002 filtered
  high-card dim w/o inverted index (MEDIUM), PIN003 group-by-heavy
  observed queries w/o star-tree (MEDIUM); DRU000 census, DRU001 no
  partitionsSpec, DRU002 rollup=false on ≥3 metrics+dims.
- `src/forge_doctor_data/cli/analytical.py` — **new**: `analytical inspect`
  renders per-engine tables/schemas, keeper files, observed, unparsed.
- `src/forge_doctor_data/analyzers/platform_graph_builder.py` —
  `_analytical` adapter: `table:<engine>:*` entities (kind +
  engine/type attrs), `schema:pinot:*` entities.
- `src/forge_doctor_data/checks/__init__.py`,
  `src/forge_doctor_data/cli/__init__.py`,
  `src/forge_doctor_data/core/incremental.py` (`SQL | CONFIG | FILES`),
  `src/forge_doctor_data/core/ontology.py` + `docs/ontology.md`
  (`clickhouse`, `druid`, `pinot` producer domains) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/clickhouse/unkeyed/` (CH001–004), `labs/pinot/rt-gap/`
  (PIN001–003), `labs/druid/unpartitioned/` (DRU001–002),
  `labs/analytical/plain-json/` (adversarial).
- `tests/unit/test_analytical.py` — **new**, 28 tests.

## Design decisions

- **Engine allowlist, not any `ENGINE=`** — `InnoDB`, `MyISAM`,
  `Memory`, `CSV`, `Archive`, `Federated`, `Blackhole` exist in MySQL;
  attribution uses an explicit ClickHouse family list (MergeTree,
  Replicated\*/Replacing\*/Summing\*/Aggregating\*/Collapsing\*/
  Graphite\* prefixes, Distributed, Kafka, S3, …). `ENGINE=MySQL(...)`
  is included — it is a real ClickHouse engine and never appears in
  MySQL DDL.
- **ClickHouse reuses the shared sql index** — generic-dialect parse
  preserves `ENGINE`/`ORDER BY`/`PARTITION BY` in statement text, so
  no second parse pass; props are extracted by regex from the parsed
  statement text, keeping the sql index as the single SQL entry point.
- **Pinot claim needs Pinot keys** — `tableName` alone isn't enough
  (generic JSON may use it); a `tableType`/`segmentsConfig`/`tenants`
  sibling or schema `*FieldSpecs` is required. Druid needs
  `ingestionSpec`/`spec` containing `dataSchema`+`ioConfig`.
- **CH002 keeper evidence is file- or content-based** — `*keeper*`/
  `*zookeeper*` filenames or XML containing `<zookeeper>`/
  `<keeper_server>`; keeper config rarely lives in the DDL itself.
- **CH004 dedupe plan** = dedupe-family engine (`Replacing`/`Summing`/
  `Aggregating`/`Collapsing`/`VersionedCollapsing`) OR any materialized
  view — deterministic proxy for "somewhere for Kafka rows to land".
- **PIN002 filter evidence** — authored SQL `WHERE` clause mentioning
  the dim name; high-cardinality evidence is `cardinality > 1000` in
  the schema or the dim listed in `noDictionaryColumns`. MEDIUM
  confidence documented.
- **PIN003 needs observed exports** — no firing without a `pinot/`
  query-log artifact carrying ≥2 GROUP BY rows for the table.
- **DRU002 width threshold = 3** — `rollup=false` + ≥3 declared
  metrics+dimensions; deterministic, documented in docs/checks.md.
- **No closed membership** — engine key on each row; adding
  StarRocks/Doris later is additive (spec open question honored).

## Found & fixed en route

- `_DISTRIBUTED_RE` couldn't match quoted local-table args
  (`Distributed('c','db','x')`) — quote now tolerated.
- `str(True)` vs `"true"` prop mismatch on the star-tree flag —
  normalized to `.lower()` comparison.
- Kafka settings regex needed two capture groups for `findall` to
  yield key/value pairs.
- Boolean Pinot props serialize via `str()` not `json.dumps` — kept
  consistent by comparing case-insensitively.

## Validation

- `pytest tests/unit/test_analytical.py` — 28 passed.
- Labs: `clickhouse/unkeyed` fires CH001–CH004; `pinot/rt-gap` fires
  PIN001–PIN003; `druid/unpartitioned` fires DRU001–DRU002;
  `analytical/plain-json` (package.json + `ENGINE=InnoDB`/`MyISAM`)
  silent — no CH/PIN/DRU findings.
- `forge-doctor-data analytical inspect labs/pinot/rt-gap` renders tables,
  schema dims/metrics, observed rows.
- ruff format/check + mypy clean on touched files.

## Open items / boundaries

- StarRocks/Doris deferred — model is engine-keyed, additive.
- No Terraform surface for these engines — documented in spec
  (documented absence, not silently skipped).
- Observed-metadata only from exported artifacts — no live cluster
  calls, per the family constraint.
