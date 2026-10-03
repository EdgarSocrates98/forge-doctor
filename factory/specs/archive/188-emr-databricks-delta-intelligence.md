---
id: 188
title: EMR + Databricks + Delta Deep Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_platforms.py tests/unit/adversarial/test_platforms.py -x -q
  - python -m forge_doctor_data emr inspect <fixture>
  - python -m forge_doctor_data knowledge verify
---

# Phase 7 - EMR + Databricks + Delta Deep Intelligence

## Context

Phase 7 of the ten-phase Forge Doctor Data program: deepen compute/runtime/table
intelligence. Existing surface: streaming/delta endpoint detection inside
`streaming_model`, Iceberg ops via `iceberg_model`, LF model from Phase 6.
No first-class EMR, Databricks, or Delta models existed.

## Acceptance Criteria

- `EmrProjectModel` separating EMR EC2 / EMR Serverless / EMR on EKS with
  release labels, fleets, spot/on-demand, autoscaling, dynamic allocation,
  roles, bootstrap, logging, security configuration, steps/failure actions;
  Terraform + CloudFormation + boto3 evidence.
- `DatabricksProjectModel`: jobs, clusters, DBR versions, serverless,
  SQL warehouses, Unity Catalog objects (external locations, storage
  credentials, volumes, catalogs, schemas), pipelines, asset bundles.
- `DeltaProjectModel`: tables, MERGE/UPDATE/DELETE/OPTIMIZE/VACUUM/RESTORE/
  CLUSTER BY ops, deletion vectors, CDF, liquid clustering, column mapping,
  schema evolution, protocol floors, streaming endpoints, CDF consumers.
- Check families EMR###, DBX###, DELTA### with deterministic evidence;
  no cloud calls; absence of evidence never becomes a negative claim.
- Cross-domain rules: EMR + Iceberg writes + Lake Formation auth (PLAT008),
  Databricks runtime + Delta feature floor (PLAT009); capability-gated.
- Platform graph integration: emr/databricks compute entities, UC
  catalog/location/principal entities, delta tables + per-op edges.
- Knowledge packs `capabilities/{emr,databricks,delta}.json` +
  `emr/releases`, `databricks/runtime`, `delta/features` (schema-2,
  provenance, HTTP(S) sources).
- CLI groups `emr`, `databricks`, `delta` with inspect/findings (+features).
- Unit + adversarial tests; insertion-order determinism; no FPs on
  substring names, comment/string spoofs, or missing evidence.

## Constraints

- stdlib-first; no cloud/API calls; no LLM; no execution of target code.
- One functional commit: `feat(platforms): deepen EMR Databricks and Delta intelligence`.
- Do not archive this spec on completion — human review gate.

## Review Notes

- hcl_lite `//` comment-stripper was made quote-aware (was truncating
  `s3://` values) — a shared-parser fix folded into this phase.
- `aws_emrserverless_application` (provider's real type) and the
  `aws_emr_serverless_application` variant are both accepted.
