# Run: Program K wave 3c — Search platforms (spec 220)

- **Initial HEAD**: `a9e03f1` (analytical engines)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/220-search-platforms.md`

## Scope

OpenSearch + Elasticsearch over a shared `SearchPlatformModel`:
index templates/mappings/settings JSON, ISM/ILM policies, ingest
pipelines, Terraform domain resources, observed cluster exports.
One shared `SRCH###` check family per the spec's open-question
decision (vendor named in messages).

## Files changed

- `src/forge_doctor/analyzers/search_model.py` — **new**:
  `SearchPlatformModel` (`SearchIndex`/`SearchPolicy`/`IngestPipeline`/
  `SearchDomain`/`ObservedRow`), compound-gated JSON attribution,
  `_field_types` mapping walker (leaf types, open nested objects,
  dynamic), Terraform domain scanner, observed-export scanner.
- `src/forge_doctor/checks/search.py` — **new**: SRCH000 census,
  SRCH001 prod-pattern template w/o replicas, SRCH002 wildcard/logs-*
  w/o ISM-ILM coverage, SRCH003 >5 open nested objects w/o
  `enabled:false`/`dynamic:false|strict`, SRCH004 TF domain w/o
  encrypt_at_rest/node_to_node_encryption.
- `src/forge_doctor/cli/search.py` — **new**: `search inspect` prints
  indices/templates, lifecycle policies, pipelines, TF domains,
  observed + unparsed rows.
- `src/forge_doctor/knowledge/capabilities/search.json` — **new**:
  VECTOR_SEARCH_KNN per vendor (ES gated on `version >= 8.0`),
  LIFECYCLE_MANAGEMENT (ISM vs ILM), SERVERLESS_DEPLOYMENT,
  INGEST_PIPELINES.
- `src/forge_doctor/analyzers/platform_graph_builder.py` — `_search`
  adapter: `table:search:*` for indices/templates,
  `infrastructure_resource:search:*` for TF domains.
- `src/forge_doctor/checks/__init__.py`,
  `src/forge_doctor/cli/__init__.py`,
  `src/forge_doctor/core/incremental.py` (`CONFIG | TERRAFORM | FILES`),
  `src/forge_doctor/core/ontology.py` + `docs/ontology.md` (`search`
  producer domain) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/search/prod-no-replicas/` (SRCH001–004 all fire),
  `labs/search/adversarial/` (bare `"mappings"`/`"policy"` keys in
  unrelated JSON stay silent).
- `tests/unit/test_search.py` — **new**, 21 tests.

## Design decisions

- **Shared `SRCH` prefix** — spec's open question prefers one family
  since most rules are vendor-common; the vendor string rides in each
  message/evidence.
- **Compound JSON attribution** — index/template claims need a filename
  or directory hint *plus* a search key shape (`index_patterns`,
  `template{settings|mappings}`, `settings.index.*`, `mappings` with
  `properties`/`dynamic`); a bare `mappings` key alone never
  attributes (adversarial lab).
- **Vendor by key shape first** — `ism_template`/`states` →
  opensearch; `phases` → elasticsearch; then filename/dir hints; else
  the neutral `search` marker. Checks never branch on vendor.
- **TF domains are exact resource types** — `aws_opensearch_domain` /
  `aws_elasticsearch_domain` / `elasticsearch_domain` /
  `opensearch_domain` / `aws_opensearchserverless_collection` /
  `aws_elasticsearch_cluster`; sub-resources
  (`aws_opensearch_domain_policy`, `..._saml_options`) aren't domain
  surfaces. Encryption/TLS read by regex over the raw block body
  (nested HCL blocks aren't flat attrs).
- **SRCH003 threshold = 5 open objects** — deterministic, documented
  in `docs/checks.md`; `enabled:false` on an object or
  `dynamic=false|strict` clears it.
- **SRCH002 coverage match is prefix-based** — a policy's
  `index_patterns` strip to a prefix that must cover the template's
  wildcard pattern; wildcard policy patterns cover all.
- **Capability versions via conditions** — ES kNN uses
  `version gte 8.0` (absent → CONDITIONAL, <8 → CONDITIONAL);
  exact-key `versions` maps can't express major-version ranges.

## Found & fixed en route

- Lab template JSON was malformed on first write — caught by the scan
  not claiming it (model.debugged via `m.indices` listing only the
  valid template). Fixed the fixture.
- `_DISTRIBUTED`-style regex lessons applied: keep claim gates cheap
  (substring pre-filter before `json.loads`), require sibling keys.
- `terraform_model` nested blocks don't surface as flat attrs —
  encryption flags come from raw `body` regex (same pattern as the
  BigQuery adapter's partition detection).

## Validation

- `pytest tests/unit/test_search.py` — 21 passed.
- Lab `prod-no-replicas` fires SRCH001–SRCH004; `adversarial` silent
  (SRCH000 PASS only).
- Capability registry — pack loads clean; `VECTOR_SEARCH_KNN`
  evaluates SUPPORTED (opensearch / es 8.11), CONDITIONAL (es 7.17,
  absent version).
- `forge-doctor search inspect labs/search/prod-no-replicas` renders
  templates, domains, encryption flags.
- ruff format/check + mypy clean on touched files.

## Open items / boundaries

- `OS`/`ES` per-vendor prefixes rejected per spec open question —
  shared `SRCH` chosen; vendor in message.
- No live cluster calls — observed metadata strictly from exports.
