---
id: 230
title: Deep Platform Ontology — abstract architectural semantics
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k ontology -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program L — Deep Platform Ontology (prompt_evo_step7 §Phase 1)

Roadmap order: after spec 229; first phase of the post-warehouse program.
The ontology today is a documented vocabulary over EntityKind/RelKind/
EvidenceKind. This spec evolves it to model abstract platform concepts.

## Context

`core/ontology.py` is a documented registry (Term + defs + vocabulary() +
validate_graph). Vendor adapters each carry their own private notion of
"what kind of thing is this" (abstractions.py has a free-text
`abstraction` field). Phase 1 adds the formal, vendor-neutral semantic
layer on top — additive only; EntityKind is not replaced.

## Acceptance Criteria

- `PlatformKind` enum — vendor-neutral implementation classes:
  COMPUTE, WAREHOUSE, LAKEHOUSE, OBJECT_STORAGE, TABLE_FORMAT, STREAM,
  MESSAGE_BUS, ORCHESTRATOR, TRANSFORMATION_ENGINE, QUERY_ENGINE,
  SEARCH_INDEX, GRAPH_STORE, OPERATIONAL_STORE, CATALOG,
  GOVERNANCE_PLANE, METADATA_CATALOG, QUALITY_SYSTEM, SERVING_LAYER,
  OBSERVABILITY_SYSTEM.
- `PlatformImplementation` model — id, vendor, product, platform_kind,
  version, deployment_mode, capabilities, aliases. Registry maps the
  platforms the analyzers already detect (glue→COMPUTE, snowflake/
  bigquery/redshift→WAREHOUSE, trino→QUERY_ENGINE, opensearch→
  SEARCH_INDEX, …). Unknown vendor → UNKNOWN, never guessed.
- `WorkloadIntent` enum (12 values: BATCH_ANALYTICS … GRAPH_ANALYTICS)
  and `DataAccessPattern` enum (12 values: POINT_LOOKUP … RANDOM_READ).
- `PhysicalDesign` model — partitioning, clustering, ordering,
  distribution, sharding, replication, indexing, materialization,
  caching, retention; mapped for bigquery/redshift/clickhouse/
  opensearch/snowflake evidence already collected.
- `MaterializationKind` enum (VIEW, MATERIALIZED_VIEW,
  INCREMENTAL_TABLE, COPY, CACHE, PROJECTION, SEARCH_INDEX, REPLICA) —
  maps dbt incremental, Snowflake dynamic tables, BigQuery MVs,
  ClickHouse projections, OpenSearch indices.
- `ConsistencyModel` enum (STRONG, EVENTUAL, READ_AFTER_WRITE,
  SNAPSHOT, REGION_LOCAL, MULTI_REGION, UNKNOWN) — never inferred
  without evidence.
- `ServingModel` — latency/freshness/availability targets + consistency
  + query_pattern + workload_intent; represents OpenSearch/ClickHouse/
  DynamoDB without flattening everything to "table".
- `DataMovement` + `DataMovementMode` (SHARE, REPLICATE, COPY,
  FEDERATE, MIRROR, STREAM) — sharing vs replication vs copy are never
  conflated.
- `LogicalDataset` → `PhysicalRepresentation` unification only on
  explicit evidence — no fuzzy matching, no name similarity.
- `AssetOwnership` (team, source, declared_by, confidence, conflicts)
  with sources: platform-contract, Terraform tags, dbt meta, DataHub,
  OpenMetadata, CODEOWNERS.
- `LifecycleModel` — created/retained/archived/compacted/snapshotted/
  expired/deleted; maps Iceberg expiration, OpenSearch ISM, ClickHouse
  TTL, BigQuery partition expiration, Snowflake retention.
- CLI under existing `ontology` group: `platform`, `workloads`,
  `access-patterns`, `validate`.
- JSON-serializable, deterministic ordering throughout.

## Constraints

- Deterministic, offline, no cloud calls, no LLM. Honest UNKNOWN.
- Public API additive — existing `forge_doctor_data.api`/sdk/schemas/SARIF/
  JSONL unchanged.
- §12/§13 rules apply: no fuzzy joins; analyzers never touch
  os.environ/Path.home()/shutil.which() — all via ProjectContext.

## Test requirements (§1.15/§14)

vendor mapping, unknown vendor, logical/physical mapping,
materialization mapping, sharing-vs-replication distinction, ownership
conflicts, deterministic serialization, ontology conformance; plus
malformed input / unknown-state coverage.
