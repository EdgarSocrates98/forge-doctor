---
id: 212
title: Warehouse Project Model (vendor-neutral core)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k warehouse -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 1a - Warehouse Project Model

## Context

Coverage expansion starts with analytics warehouses (Snowflake,
BigQuery, Redshift). The doc's key architectural decision: build the
**generic model first** — a `WarehouseProjectModel` that vendor adapters
(SNOW001*, BQ*, RS*) populate — rather than three parallel vendor silos.
This is the foundation spec: semantic model + graph adapter + generic
checks, before any vendor adapter (213/214/215).

## Acceptance Criteria

- `analyzers/warehouse_model.py` — `WarehouseProjectModel` built from
  normalized evidence, with (all optional, discovered incrementally):
  `platform` (vendor id), `compute` (warehouses/clusters/slots),
  `databases`, `schemas`, `tables`, `views`, `materialized_views`,
  `external_tables`, `workload_management` (queues/reservations/WLM),
  `queries` (observed or authored), `security` (roles/grants),
  `sharing` (publication/consumer links), `costs` surfaces.
- `WarehouseProjectModel` derives from `ctx` models only when warehouse
  evidence exists (empty model otherwise — no false positives on
  non-warehouse projects).
- Graph adapter maps the model into the `DataPlatformGraph` as
  `warehouse`, `warehouse_compute`, `table`, `view`, `schema`,
  `database` entities with `CONTAINS`/`READS_FROM`/`WRITES_TO` edges.
- `platform`/`capability` registry gains the `warehouse` family so
  `capabilities_evaluate` and `what-if` can query warehouse capability
  surfaces once vendor packs land.
- Generic `WARE###` checks only where vendor-neutral semantics exist
  (e.g., observed table with no storage stats flagged as unprofiled).
- Lab scenario `labs/warehouse/*` with ground truth.

## Constraints

- Vendor specifics belong in adapters (213-215), not this model. If a
  field only makes sense for one vendor it must live behind
  `attrs`/vendor passthrough, not a top-level field.
- Deterministic, offline, no target-code execution — same evidence
  pipeline as every other analyzer.

## Open Questions

- Should `costs` be populated from observed metadata only (bytes
  scanned, slot ms) or also pricing tables? Pricing tables go stale;
  keep them advisory-in-docs until decided.
