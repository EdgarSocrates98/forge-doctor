---
id: 218
title: Trino federated-SQL adapter
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k trino -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 3 - Trino / federated SQL

## Context

Wave 3 = federated/distributed SQL. Trino evidence is config-centric:
`etc/catalog/*.properties` (connector per catalog), `config.properties`
(coordinator/worker), `node.properties`, `jvm.config`, plus authored
SQL using `catalog.schema.table` three-part names. Presto variants are
explicitly deferred (open question in doc).

## Acceptance Criteria

- `analyzers/trino_model.py` — `TrinoProjectModel`: catalogs with
  connector type (`hive`, `iceberg`, `delta`, `jdbc`, `kafka`, `tpch`…),
  coordinator/worker settings (query.memory limits, spill config,
  resource groups, event listeners), observed metadata (cluster info
  JSON exports optional).
- Capability pack: per-connector capability surfaces (reads, writes,
  pushdown, transactional) feeding `capabilities_evaluate`.
- `TRINO###` checks: `TRINO001` hive catalog without metastore config;
  `TRINO002` no spill-to-disk config on coordinator running ETL-style
  queries; `TRINO003` `tpch`/`jmx`/`system` connector catalog files in
  a production-looking deployment; `TRINO004` resource groups absent
  on multi-catalog deployment; `TRINO005` three-part SQL referencing a
  catalog not present in config (static cross-check).
- CLI `trino inspect .`; lab suite `labs/trino/*` + adversarial case.

## Constraints

- `.properties` parsing is a small deterministic format — implement
  without a JVM dependency; tolerate `key=value` + comments.
- Athena federation / Spark SQL federation from the doc's wave-3 list:
  already partly covered by existing packs — extend only if a gap is
  demonstrated; record the boundary in the spec, don't duplicate.

## Open Questions

- Presto (distinct coordinator semantics) — deferred to its own spec
  once Trino lands; do not branch on "presto" detection now.
