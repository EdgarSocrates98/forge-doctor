---
id: 234
title: Migration Intelligence 2.0 — ontology-driven, lossiness-aware
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "migration or sqlport" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program P — Migration Intelligence 2.0 (prompt_evo_step7 §Phase 5)

Depends on 224 (cross-platform migration), 230 (ontology), 231
(capability deps). The current version uses explicit source→target maps
that grow N×N; this replaces the core with ontology-driven mapping.

## Context

`core/crossmigration.py` holds `_ECOSYSTEM` maps + `_CHANGE_NOTES` per
(service, abstraction) and builds `PlatformMigrationPlan` with staged
ordering + MIGR findings. Phase 5 introduces concept-level mapping and
SQL portability analysis.

## Acceptance Criteria

- `MigrationConcept` — logical_concept, source_implementation,
  target_implementation, semantic_gap, operational_gap, confidence.
- Mapping types kept/deepened: DIRECT, APPROXIMATE, REDESIGN_REQUIRED,
  NO_EQUIVALENT, UNKNOWN. Lossiness: LOSSLESS, SEMANTIC_CHANGE,
  OPERATIONAL_CHANGE, PERFORMANCE_CHANGE, SECURITY_CHANGE,
  MANUAL_REDESIGN.
- Ontology-driven flow: source entity → PlatformKind → required
  capability set → target candidate capability set → mapping. Avoid
  hardcoded source_vendor × target_vendor matrices wherever the
  capability layer answers the question.
- `SqlDialectCapabilities` for snowflake, bigquery, redshift, trino,
  spark, databricks, clickhouse. Portability findings: SQLPORT001
  dialect function, SQLPORT002 MERGE semantics, SQLPORT003 QUALIFY,
  SQLPORT004 timestamp/timezone, SQLPORT005 identifier quoting,
  SQLPORT006 nested/semi-structured, SQLPORT007 NULL ordering.
- `SchemaCompatibility` — source_type, target_type, nullability,
  precision, nested_shape, coercion, compatibility. Covers BigQuery
  STRUCT / Snowflake VARIANT / Trino ROW / ClickHouse Tuple /
  OpenSearch object.
- Security migration (roles, RLS, masking, sharing, cross-account,
  identity), governance migration (Lake Formation, Unity Catalog,
  Purview, Dataplex, Snowflake governance, BigQuery IAM), operational
  (scheduler, compute, autoscaling, retry, observability, SLAs), cost
  drivers (credits/slots/capacity — drivers only, never exact cost).
- Runtime-informed readiness when runtime evidence exists;
  `MigrationReadiness` — READY, PARTIAL, BLOCKED,
  INSUFFICIENT_EVIDENCE; unknown budget (known_count, unknown_count,
  unknowns, required_evidence).
- `forge-doctor migrate explain <plan>` — why mapped / why approximate
  / which capability missing / which source evidence proves it.
- Deep scenarios: Redshift→Snowflake/BigQuery, BigQuery→Snowflake,
  Snowflake→BigQuery, Glue→Databricks, EMR→Databricks, Delta→Iceberg,
  Snowflake→Iceberg lakehouse, AWS→Azure, AWS→GCP, Azure→GCP.

## Constraints

- Fact vs conclusion (§11): "supports X" is fact; "migration preserves
  behavior" is conclusion requiring fact+context+deps+evidence.
- Never claim exact cost. Prefer INSUFFICIENT_EVIDENCE over guessing.
- Existing `migrate plan` CLI + JSON shape stays compatible; new fields
  additive.

## Test requirements

Per §14 suite per model + scenario-level expected readiness/unknowns.
