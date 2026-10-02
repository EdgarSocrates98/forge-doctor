---
id: 214
title: BigQuery adapter + capability pack
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k bigquery -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 1c - BigQuery

## Context

Second warehouse adapter on `WarehouseProjectModel` (212). Evidence:
BigQuery DDL (`CREATE SCHEMA|TABLE|VIEW|MATERIALIZED VIEW` with
`PARTITION BY`/`CLUSTER BY`/OPTIONS), Terraform `google_bigquery_*`
resources, observed `INFORMATION_SCHEMA` exports (tables, partitions,
jobs-by-project for slot cost signals).

## Acceptance Criteria

- `analyzers/bigquery_model.py` — platform=`bigquery`; datasets
  (schemas), tables with partition/cluster fields, views + materialized
  views, slots/reservations as `workload_management`, authorized views,
  external tables (BigLake).
- Capability pack: partitioning, clustering, BI Engine, slots vs
  on-demand, time travel window, BigLake, DML limits — via
  `capabilities_evaluate`.
- `BQ###` checks, minimum set: `BQ001` large observed table without
  partitioning; `BQ002` partitioned table queried without partition
  filter (from authored SQL or observed jobs); `BQ003` `SELECT *` on
  columnar-billed engine flagged cost-risky; `BQ004` public/external
  dataset or authorized-view without doc; `BQ005` materialized view
  over mutable base table without staleness policy.
- CLI `bigquery inspect .`.
- Lab suite `labs/bigquery/*` + adversarial case (plain SQL files must
  not trigger BQ findings without bigquery evidence).
- `WARE`-family graph entities populated; dataset→table `CONTAINS`,
  view→table `READS_FROM`.

## Constraints

- Same evidence discipline: static DDL, IaC, observed exports — never
  call GCP APIs.

## Open Questions

- Should `BQ002` require observed job history (higher confidence) or
  allow authored-SQL-only warnings at lower confidence? Default:
  authored SQL = warning, observed jobs = error.
