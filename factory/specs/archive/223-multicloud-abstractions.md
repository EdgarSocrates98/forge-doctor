---
id: 223
title: Multi-cloud abstractions (AWS/Azure/GCP common layer)
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "multicloud or abstraction" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 6 - Vendor-neutral platform abstractions

## Context

Doc wave 6 = multi-cloud. Long-term goal from the strategy doc:
vendor-neutral abstractions — `ObjectStorage`, `ComputeEngine`,
`Catalog`, `Stream`, `OperationalStore`, `Warehouse` — with vendors as
implementations. This spec normalizes existing AWS coverage plus new
Azure/GCP signals into those abstractions WITHOUT breaking current
checks.

## Acceptance Criteria

- `core/abstractions.py` (or `analyzers/abstractions.py`) mapping layer:
  `ObjectStorage` ← s3|adls_gen2|gcs; `Stream` ← kinesis|eventhubs|
  pubsub|msk/confluent; `ComputeEngine` ← emr|synapse|databricks|
  dataproc|glue; `Catalog` ← glue|purview|unity|datacatalog;
  `OperationalStore` ← dynamodb|cosmosdb|bigtable; `Warehouse` ←
  redshift|synapse_sql|bigquery|snowflake|fabric.
- Terraform model recognizes `azurerm_*`/`google_*` data-platform
  resources into the abstraction layer (minimal resource set: storage
  accounts, adls, eventhubs, pubsub topics, dataproc clusters,
  bigquery datasets, cosmosdb, synapse workspaces, purview accounts).
- `CLOUD###` checks only where cross-cloud parity rules exist:
  `CLOUD001` platform entity with no abstraction mapping (coverage
  blind spot — internal signal); `CLOUD002` mixed-cloud project where
  equivalent service exists in one cloud only without a declared
  replication/migration link (drift risk — info level).
- Capability registry extended so `capabilities_evaluate` can answer
  cloud-agnostic questions (`object_storage.versioning` → per-cloud
  surface).
- Lab suites: azure-style, gcp-style, mixed-aws-azure fixtures.

## Constraints

- Existing check ids and semantics must not change — abstractions are
  a *view over* existing entities, not a replacement.
- Risk=high because this touches the shared model; keep the adapter
  layer additive and behind tested seams.

## Open Questions

- Resource-attribute normalization depth: full attr parity is huge —
  default to top-level attrs (name, region, encryption, public
  exposure) + vendor attrs passthrough.
