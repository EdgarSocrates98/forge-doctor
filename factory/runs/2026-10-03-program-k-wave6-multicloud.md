# Run: Program K wave 6 — Multi-cloud abstractions (spec 223)

- **Initial HEAD**: `9d20f7d` (data quality)
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/223-multicloud-abstractions.md`

## Scope

Vendor-neutral abstraction *view* over existing entities — six
abstractions (`object_storage`, `stream`, `compute_engine`, `catalog`,
`operational_store`, `warehouse`) with vendors as implementations.
Existing check ids/semantics untouched (spec constraint).

## Files changed

- `src/forge_doctor_data/analyzers/abstractions.py` — **new**:
  `CloudAbstractionModel` (`AbstractedService`/`UnmappedService`), TF
  resource map for `azurerm_*`/`google_*`/`aws_*` data-platform
  resources, graph-entity fold (warehouse domain via `platform` attr,
  kinesis/kafka/dynamodb/glue/lakeformation domains), shallow attr
  normalization (name/region/encrypted/public + ≤8 passthrough attrs),
  link detection on attr keys *and* nested block bodies.
- `src/forge_doctor_data/checks/cloud.py` — **new**: CLOUD000 census,
  CLOUD001 unmapped platform entities (info, capped 15), CLOUD002
  single-cloud kind in mixed estate without replication/migration link.
- `src/forge_doctor_data/cli/cloud.py` — **new**: `cloud inspect` prints
  services by abstraction, cloud attrs, estate clouds, unmapped list.
- `src/forge_doctor_data/knowledge/capabilities/cloud.json` — **new**:
  cloud-agnostic capability surface (VERSIONING/ENCRYPTION per object
  store, TIME_TRAVEL/ZERO_COPY_CLONE per warehouse service, FGAC per
  catalog, TTL for operational stores, serverless per compute engine) —
  entries keyed `platform=<abstraction>` with `when: {service: ...}`.
- `src/forge_doctor_data/checks/__init__.py`,
  `src/forge_doctor_data/cli/__init__.py`,
  `src/forge_doctor_data/core/incremental.py` (`TERRAFORM | UNBOUNDED`),
  `src/forge_doctor_data/core/ontology.py` + `docs/ontology.md` (`cloud`
  producer domain) — registrations.
- `docs/checks.md`, `README.md`, `CHANGELOG.md`.
- `labs/cloud/{azure-only,gcp-only,mixed-parity,linked}/`.
- `tests/unit/test_cloud.py` — **new**, 12 tests.

## Design decisions

- **View, not entities** — the spec constraint ("abstractions are a
  view over existing entities, not a replacement") means no new graph
  kinds/domains; the model folds TF resources + warehouse entities.
- **`service` is the cloud-agnostic condition axis** — pack entries use
  `platform=<abstraction>` + `when: {service: ...}`; absent service
  falls through to the vendor-gated warehouse pack → `conditional`
  (honest "needs the service attribute").
- **Link detection covers nested blocks** — `_LINK_RE` scans attr keys
  *and* raw `block.body` so `geo_location { failover_priority }`
  suppresses CLOUD002 (same nested-block lesson as BigQuery/Trino).
- **CLOUD002 needs ≥2 real clouds** — `vendor`-neutral rows (kafka)
  never count as a cloud; single-cloud estates stay silent.
- **CLOUD001 is bounded** — platform domains only
  (warehouse/kinesis/kafka/dynamodb/glue/lakeformation/neptune/trino/
  analytical/search/metadata × data-bearing kinds), info severity,
  capped 15 — internal signal, not user debt.

## Found & fixed en route

- `warehouse/TIME_TRAVEL` already existed in the spec-212 pack gated on
  `vendor` — absent-service evaluation resolves `conditional` there;
  the cloud pack's `service`-gated entries extend rather than collide.
- `TfBlock.body` carries nested blocks — `geo_location`/`failover`
  links need the body scan, flat `attrs` don't surface them.

## Validation

- `pytest tests/unit/test_cloud.py` — 12 passed (incl. capability
  cloud-agnostic evaluation: snowflake→supported, redshift→
  unsupported, absent service→conditional).
- Labs: `azure-only`/`gcp-only` single-cloud silent on CLOUD002;
  `mixed-parity` fires CLOUD002 for stream+operational_store;
  `linked` suppressed via `geo_location`/`failover_priority`.
- `forge-doctor-data cloud inspect labs/cloud/mixed-parity` renders the
  abstraction view with cloud split.
- ruff + mypy clean on touched files.

## Open items / boundaries

- No `azurerm_*`/`google_*` *check* coverage — resources feed the
  abstraction view only; per-vendor depth is a later wave.
- `confluent`/`fabric` services exist in the map but have no TF
  resource types wired (platform domains only via graph fold).
- Link semantics are key-name heuristics — a `replication` attr that
  isn't actually cross-cloud still suppresses; documented tradeoff.
