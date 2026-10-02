# Run: Program K wave 3d — Metadata catalogs (spec 221)

- **Initial HEAD**: `1c7b89b` (search platforms)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/221-metadata-catalogs.md`

## Scope

Catalog/metadata/governance adapters over a `MetadataEstateModel`:
DataHub and OpenMetadata export parsing, Glue Data Catalog and Unity
Catalog presence/coverage signals, ingestion-recipe connector types
(secrets never ingested), and catalog↔platform drift checks.

## Files changed

- `src/forge_doctor/analyzers/metadata_model.py` — **new**:
  `MetadataEstateModel` (`CatalogDataset`/`IngestionRecipe`), DataHub
  export parsing (dataset URNs → platform/env/qualified name, aspect
  arrays → owners/description/tags/glossary/schema/upstreams),
  OpenMetadata entity parsing (`fullyQualifiedName`, owner dicts,
  tagFQN tags), Glue/Unity presence+coverage under `glue*/`/`unity*/`
  path hints, recipe scanner emitting connector `type` values only.
- `src/forge_doctor/checks/metadata.py` — **new**: META000 census,
  META001 stale catalog entry, META002 coverage gap (capped at 20,
  info), META003 ownerless dataset, META004 prod asset without
  description/tags, META005 declared-vs-detected lineage contradiction.
- `src/forge_doctor/cli/catalog.py` — **new**: `catalog inspect`
  prints vendor, datasets (env/owners/tags/upstreams), recipes
  (connector types only), lineage, unparsed files.
- `src/forge_doctor/analyzers/platform_graph_builder.py` — `_metadata`
  adapter: `dataset:metadata:*` entities (domain `metadata`,
  `GOVERNS`-style declaration) so catalog datasets join the canonical
  graph without self-matching drift checks.
- `src/forge_doctor/checks/__init__.py`,
  `src/forge_doctor/cli/__init__.py`,
  `src/forge_doctor/core/incremental.py`,
  `src/forge_doctor/core/ontology.py` + `docs/ontology.md` (`metadata`
  producer domain) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/catalog/stale-entry/` (META001/003/004 fire),
  `labs/catalog/adversarial/` (generic JSON stays silent).
- `tests/unit/test_metadata.py` — **new**, 20 tests.

## Design decisions

- **Both directions are findings** — META001 (declared absent from
  graph) and META002 (detected absent from catalog) are symmetric; each
  message names the driving side. Neither auto-fixes.
- **Metadata domain entities can't self-match** — catalog datasets join
  the graph as `domain="metadata"`; coverage comparisons exclude that
  domain so a stale entry doesn't satisfy itself.
- **Vendor attribution needs strong evidence** — DataHub: filename
  `*.datahub.json` or `urn:li:`/`entityUrn` shapes; OpenMetadata:
  `*.ometa.json` or `entityType`+`fullyQualifiedName`; Glue/Unity: path
  hints only (presence/coverage, no deep modeling per spec). Generic
  JSON with metadata-looking keys never attributes.
- **Recipes carry connector types only** — YAML/JSON recipe files are
  parsed (via `_load_yaml`/`json.loads`) and only `source.type` /
  `sink.type` retained; `config`/credential blocks are discarded at
  parse time.
- **Lineage mediation** — catalog upstreams compare against detected
  lineage resolved through the SQL adapter's query-mediated edges
  (`query READS table`, `query WRITES table` → two-hop upstream set)
  plus direct `READS_FROM` edges from dbt/other adapters.
- **META002 capped at 20** — coverage gaps on large estates are
  informational, not per-entity incidents.

## Found & fixed en route

- `_load_yaml` returns a `(doc, err)` tuple, not the doc — the recipe
  loader discarded every YAML file (recipes came back empty). Unpack
  the tuple.
- `_str_list` split on `:` which mangled `urn:li:` values — upstreams
  and owner URNs now bypass generic string splitting (raw extraction).
- DataHub owner shape `{"owner": "urn:..."}` and tag shape
  `{"tag": "urn:..."}` needed dict-key handling; OpenMetadata `owner`
  is a single dict, not a list.
- `DataPlatformGraph` lives in `core/platform_graph` (not the builder
  module) — TYPE_CHECKING import path corrected.
- `RelKind` lives in `core/platform_graph` — same fix in checks.

## Validation

- `pytest tests/unit/test_metadata.py` — 20 passed.
- Lab `stale-entry` fires META001/003/004 (ghost table stale, orders
  ownerless+undocumented prod); `adversarial` silent.
- `forge-doctor catalog inspect labs/catalog/stale-entry` renders
  vendor, env, owners, tags, upstreams — no secret material.
- ruff format/check + mypy clean on touched files.

## Open items / boundaries

- Glue/Unity intentionally shallow — presence + coverage only; deeper
  modeling is out of spec.
- META005 needs query-mediation resolution — lineage on a platform
  without SQL evidence (no readable writes) can only compare direct
  READS_FROM edges.
- No live catalog calls — strictly offline exports.
