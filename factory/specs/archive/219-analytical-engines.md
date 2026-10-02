---
id: 219
title: Analytical engines model (ClickHouse, Pinot, Druid)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "analytical or clickhouse or pinot or druid" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 3b - Real-time OLAP engines

## Context

Doc wave 3 also covers ClickHouse, Pinot, Druid — real-time/serving
engines that share a shape distinct from warehouses: denormalized
tables, partitioning/ordering keys, ingestion specs (batch + streaming),
segments. Build a shared `AnalyticalEngineModel` + three thin adapters,
like the warehouse family.

## Acceptance Criteria

- `analyzers/analytical_model.py` — `AnalyticalEngineModel`: engine,
  tables/collections with engine+keys, ingestion specs (Kafka/batch),
  replication/sharding, materialized projections/rollups, observed
  metadata (system tables / segment metadata exports).
- ClickHouse adapter: `CREATE TABLE ... ENGINE = MergeTree|Replicated*|
  Distributed`, `ORDER BY`/`PARTITION BY` keys, projections; Terraform
  absent (documented). `CH###` checks: `CH001` MergeTree table without
  ORDER BY; `CH002` Replicated* engine without keeper config;
  `CH003` Distributed table without local shard definition; `CH004`
  ingestion from Kafka with no dedupe/ordering key plan.
- Pinot adapter: table config + schema JSON (`*.table.json`,
  `*schema.json`), realtime/offline split, stream ingestion spec,
  indexes (sorted/inverted/star-tree). `PIN###` checks: `PIN001`
  realtime table without retention; `PIN002` high-cardinality dim
  without inverted index where filter evidence exists; `PIN003`
  star-tree index absent on group-by-heavy observed queries.
- Druid adapter: ingestion specs (`ingestionSpec` JSON), datasources,
  `segments` observed metadata, rollup granularity. `DRU###` checks:
  `DRU001` datasource without partitioning config; `DRU002` rollup
  disabled on high-cardinality metrics.
- One lab suite per engine; shared adversarial suite proving plain
  JSON config files do not fire engine rules.

## Constraints

- These adapters ship behind the same evidence discipline; observed
  metadata only from exported artifacts.

## Open Questions

- StarRocks/Doris (same family) — defer; the shared model must not
  assume ClickHouse/Pinot/Druid are the only members.
