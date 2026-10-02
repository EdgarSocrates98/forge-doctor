# Run: Program K wave 2a — dbt adapter (spec 216)

- **Initial HEAD**: `0703113` (Redshift adapter)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/216-dbt-adapter.md`

## Scope

Transformation-layer adapter — the first non-warehouse domain in
Program K. Parses the dbt project surface (`dbt_project.yml`,
`profiles.yml`, `schema.yml` properties, model `.sql`, seeds,
snapshots, singular tests, macros, exposures) plus observed
`target/manifest.json` / `run_results.json`. DBT001–005 checks,
`dbt inspect`, graph lineage for `ref()`/`source()`.

## Files changed

- `src/forge_doctor/analyzers/dbt_model.py` — **new**:
  `DbtProjectModel` (project name/profile/model dirs; models with
  materialization, `unique_key`, refs/sources, tests, description;
  sources with freshness; seeds/snapshots/singular tests/exposures/
  macros; manifest node count; run-result rows; unparsed).
- `src/forge_doctor/checks/dbt.py` — **new**: DBT000 census, DBT001
  untested model, DBT002 incremental without `unique_key`, DBT003
  source without freshness, DBT004 declared-but-unused source, DBT005
  doc coverage < 50%.
- `src/forge_doctor/cli/dbt.py` — **new**: `dbt inspect`.
- `src/forge_doctor/analyzers/platform_graph_builder.py` — `_dbt`
  adapter: `dbt_model` entities, `READS_FROM` edges for `ref()`/`source()`,
  `WRITES_TO` edges for materialized outputs; output/source relations
  link to warehouse entities by tail-name when adapters 213–215
  already claimed them.
- `src/forge_doctor/core/platform_graph.py`,
  `src/forge_doctor/core/ontology.py`, `docs/ontology.md` —
  `EntityKind.DBT_MODEL` + `dbt` producer domain.
- `src/forge_doctor/core/incremental.py` — `dbt` domain
  (`SQL | CONFIG | FILES`).
- `src/forge_doctor/checks/__init__.py`,
  `src/forge_doctor/cli/__init__.py` — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/dbt/basic-project/` (DBT001–004 + observed manifest),
  `labs/dbt/plain-dir/` (adversarial).
- `tests/unit/test_dbt.py` — **new**, 13 tests.

## Design decisions

- **`dbt_project.yml` (or `manifest.json`) is the attribution gate** —
  Jinja `ref()`/`source()` in stray SQL or generic `schema.yml` never
  attribute to dbt. The adversarial lab pins silence.
- **Secrets discipline**: `profiles.yml` is parsed for profile names
  and target **key names only** — values are never read into the
  model, so env-var or literal secrets cannot reach findings. Pinned
  by `test_profiles_key_names_only`.
- **YAML via the existing `_load_yaml`** (`core/contract.py`) — the
  environment has no PyYAML, so the strict mini-parser fallback is
  what actually parses schema.yml (nested lists, `warn_after` blocks,
  column `tests:` all verified working).
- **`manifest.json`/`run_results.json` are `observed` evidence** — node
  census and last-run statuses only; `dbt compile`/`run` are never
  invoked (spec constraint).
- **Graph lineage is two-pass** — all `dbt_model` entities are created
  before edge wiring so forward `ref()`s resolve (fact referencing a
  staging model ordered later in the scan).
- **Warehouse linking is a documented tail-name heuristic** — a
  `ref()`ed output named `fact_orders` links to `table:snowflake:...
  .fact_orders` only when a vendor adapter already claimed that exact
  tail; otherwise a `table:dbt:`/`view:dbt:` entity stands alone.
  Ephemeral models emit no output relation.
- **Unresolved refs are silently absent** — a `ref()` naming no parsed
  model produces no edge (consistent with the adapter's
  conservative-attribution rule).

## Found & fixed en route

- **Forward-ref edge drop**: models are scanned in path order, so
  `marts/fact_orders` preceded `staging/stg_orders` — the first pass
  found no `stg_orders` entity and dropped the `READS_FROM` edge. Fixed
  by splitting entity creation from edge wiring (two passes).
- **`g.entities` is a method** — the initial adapter iterated it as a
  property (`TypeError: 'method' object is not iterable`).
- **Accidental `_iceberg` clip**: the insertion edit briefly removed
  `iceberg_model`'s import line; restored and verified.

## Tests / gates

- `pytest tests/unit/test_dbt.py`: **13 passed**
- `pytest tests/unit/ -k dbt`: **13 passed**
- `pytest tests/ -x -q`: **1617 passed**
- `lab run`: **20/20 PASS** (incl. `basic-project` firing DBT001–004,
  adversarial `plain-dir` silent)
- ruff/mypy on touched files: clean
- `dbt inspect` on the lab: renders project/profile-resolution/
  models/sources/exposures/manifest census

## Known limitations

- Mini-YAML fallback covers the mapping/list shapes in the fixtures;
  exotic YAML (anchors, multi-line scalars, flow mappings in lists)
  lands in `unparsed` honestly rather than misparse.
- `unique_key`/`materialized` extraction is regex-on-`config(...)` —
  configs split across macro indirection under-report.
- Manifest `depends_on` isn't merged into lineage (SQL `ref()`/`source()`
  is the primary source); manifest is census evidence only.
- Semantic-layer (dbt >=1.6 `semantic_models`/`metrics`) parsing is the
  spec's open question — surfaced via `unparsed`, dedicated checks
  deferred.
- Spec remains in `active/` pending human review.
