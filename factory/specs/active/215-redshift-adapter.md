---
id: 215
title: Redshift adapter + capability pack
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k redshift -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 1d - Redshift

## Context

Third warehouse adapter on `WarehouseProjectModel` (212). Evidence:
Redshift DDL (`CREATE TABLE ... DISTSTYLE|DISTKEY|SORTKEY`, `CREATE
EXTERNAL TABLE`, `CREATE MATERIALIZED VIEW`), Terraform
`aws_redshift_cluster`/`aws_redshiftserverless_*`, observed metadata
(`SVV_*`/`STL_*` exports: table sizes, skew, WLM queue times).

## Acceptance Criteria

- `analyzers/redshift_model.py` — platform=`redshift`; provisioned
  clusters + Serverless workgroups under `compute`, databases/schemas/
  tables with dist/sort keys, materialized views, external tables
  (Spectrum), WLM queue config under `workload_management`, datashares
  under `sharing`.
- Capability pack: Spectrum, datashares, RA3 managed storage, Serverless
  RPU, concurrency scaling, auto materialized views, ATO — via
  `capabilities_evaluate`.
- `RS###` checks, minimum set: `RS001` large observed table with
  `DISTSTYLE EVEN`/`ALL` + join-heavy evidence → distribution risk;
  `RS002` table without `SORTKEY` where observed predicates filter
  ranges; `RS003` `automatic_table_optimization` off on provisioned
  cluster with skewed tables; `RS004` public cluster or `publicly_
  accessible`/`encrypted=false` Terraform flags; `RS005` manual
  `VACUUM`/`ANALYZE` scripts when ATO available.
- CLI `redshift inspect .`; lab suite `labs/redshift/*` + adversarial
  case (Postgres DDL must not fire RS rules).
- Graph + runtime-evidence adapters like 213/214.

## Constraints

- STL/SVV exports are *observed* evidence kind — findings citing them
  must mark confidence accordingly (`evidence_kind=observed`).

## Open Questions

- Cross-pack rule RS001 needs join evidence — accept SQL files in-repo
  as join signal or require observed query logs? Default: either, with
  confidence split.
