---
id: 233
title: Deep Multi-Cloud Intelligence — Azure + GCP service models
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "azure or gcp or cloud" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program O — Deep Multi-Cloud Intelligence (prompt_evo_step7 §Phase 4)

Depends on 223 (multi-cloud abstraction), 230 (PlatformKind). Azure and
GCP coverage is shallow today — detection exists via Terraform/generic
config, but there are no real per-service models like the AWS doctors.

## Context

AWS has deep per-service models (`analyzers/*_model.py`) feeding
`model.has_evidence`-style estates; `analyzers/abstractions.py` maps
everything to vendor-neutral abstractions. Phase 4 builds equivalent
depth for Azure and GCP.

## Acceptance Criteria

- Azure models: ADLS Gen2 (accounts, containers, hierarchical
  namespace, paths, ACLs, encryption, network access), Fabric/OneLake
  (workspaces, lakehouses, warehouses, semantic models, pipelines,
  shortcuts, OneLake paths — shortcut vs copy vs replication
  distinguished), Synapse (workspace, SQL pool, serverless SQL, Spark
  pools, pipelines, linked services), Event Hubs (namespace, hubs,
  partitions, consumer groups, retention, capture), ADF, Purview
  (assets, classification, lineage, ownership — integrates
  MetadataEstateModel), Azure Databricks, Azure Functions.
- GCP models: GCS (buckets, prefixes, lifecycle, storage classes,
  retention, versioning, encryption), Dataflow (pipeline, batch/stream,
  workers, autoscaling, state, windowing, watermarks, shuffle),
  Dataproc (cluster/serverless, Spark version, autoscaling, images,
  init), Pub/Sub (topics, subscriptions, ordering, dead-letter,
  retention, delivery attempts), Composer, Dataplex (lakes, zones,
  assets, governance, quality, metadata), Cloud Functions; BigQuery
  deepening where evidence exists.
- Everything maps onto PlatformKind abstractions (OBJECT_STORAGE,
  STREAM, COMPUTE, WAREHOUSE, CATALOG, GOVERNANCE_PLANE, ORCHESTRATOR).
- Graph edges: e.g. ADF pipeline INVOKES Synapse SQL; Dataflow WRITES
  BigQuery; Pub/Sub TRIGGERS Dataflow.
- Deep capability packs: azure-fabric, adls, synapse, eventhubs,
  purview, dataflow, dataproc, pubsub, dataplex — every entry sourced.
- Offline runtime-evidence adapters: Dataflow job metrics, Pub/Sub
  delivery/backlog, Synapse query, Event Hubs metrics, Fabric pipeline
  exports — files only, never API calls.
- labs/azure/ and labs/gcp/ with good/bad/cross-domain/migration/
  runtime scenarios + expected findings/entities/edges per §15.

## Constraints

- No new vendor without evidence-analyzers; detection only from repo
  artifacts (Terraform, config, code, committed exports).
- Hermetic rule §13: no os.environ/home/which inside analyzers.
- Error-severity lab findings need [[tool.forge-doctor-data.suppressions]]
  entries (RS004 precedent) so the dogfood job stays green.

## Test requirements

Per §14 (happy path, false positive/negative, malformed, cross-file,
adversarial, determinism, serialization, unknown-state) for each new
model, plus lab expected-findings conformance.
