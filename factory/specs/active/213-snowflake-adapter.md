---
id: 213
title: Snowflake adapter + capability pack
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k snowflake -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 1b - Snowflake

## Context

First vendor adapter on `WarehouseProjectModel` (spec 212). The doc
lists Snowflake evidence: SQL DDL (`CREATE WAREHOUSE|DATABASE|SCHEMA|
TABLE|STAGE|PIPE|STREAM|TASK`), Terraform `snowflake_*` resources, and
observed metadata (`SHOW`/`INFORMATION_SCHEMA` exports).

## Acceptance Criteria

- `analyzers/snowflake_model.py` — populates `WarehouseProjectModel`
  platform=`snowflake` from: Snowflake DDL in `.sql` files, Terraform
  `snowflake_*` resources (warehouse size, auto_suspend, database,
  schema, stage, pipe, task, role/grant), and observed-metadata exports
  under conventional paths (`snowflake/`, `.forge-doctor/evidence/`,
  `information_schema*` JSON/CSV).
- Capability pack entries: time travel, zero-copy clone, Snowpipe,
  streams/tasks, clustering, result caching, multi-cluster warehouses —
  exposed through `capabilities`/`capabilities_evaluate`.
- `SNOW###` checks grounded in the model, minimum set:
  `SNOW001` warehouse without `auto_suspend`; `SNOW002` warehouse
  without `auto_resume` counterpart flagged asymmetric; `SNOW003` large
  observed table without clustering keys; `SNOW004` `COPY INTO` public
  stage / insecure stage; `SNOW005` `SELECT *` in persisted procedures
  or view DDL without column list.
- CLI `snowflake inspect .` — model summary following the `iceberg
  inspect`/`delta inspect` pattern.
- Lab suite `labs/snowflake/*` with ground truth incl. one adversarial
  case (non-snowflake SQL must not trip SNOW rules).
- Graph adapter feeds `warehouse:*` entities + edges; runtime evidence
  adapter recognizes Snowflake query history exports as `runtime`
  evidence kind.

## Constraints

- Offline-first: Snowflake metadata arrives via exported artifacts only.
- Check ids/prefix reserved: `SNOW` — document in `docs/checks.md` +
  docs-coverage test.

## Open Questions

- Exact observed-metadata file conventions — keep the loader tolerant
  (accept `SHOW WAREHOUSES`-style JSON/CSV with documented fields) and
  record unknown shapes as unparsed, never dropped silently.
