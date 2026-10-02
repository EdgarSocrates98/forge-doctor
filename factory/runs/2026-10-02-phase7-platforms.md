# Run Record — Phase 7: EMR + Databricks + Delta Deep Intelligence

- Spec: `factory/specs/active/188-emr-databricks-delta-intelligence.md`
- Date: 2026-10-02
- Commit: `feat(platforms): deepen EMR Databricks and Delta intelligence`

## What was built

- `analyzers/emr_model.py` — `EmrProjectModel` over Terraform
  (`aws_emr_cluster`, `aws_emrserverless_application`,
  `aws_emrcontainers_virtual_cluster`, `aws_emr_step`,
  `aws_emr_managed_scaling_policy`), CloudFormation (`AWS::EMR::*`,
  `AWS::EMRServerless::*`, `AWS::EMRContainers::*`), and boto3
  `emr`/`emr-serverless`/`emr-containers` call-sites. Captures release
  labels, fleet/spot/on-demand splits, autoscaling, dynamic allocation,
  service/job-flow roles, bootstrap actions, `log_uri`, security
  configuration, and per-step `action_on_failure` coverage.
- `analyzers/databricks_model.py` — `DatabricksProjectModel` over the
  `databricks_*` Terraform provider (jobs incl. nested `task`/
  `job_cluster`/`new_cluster` blocks, clusters, SQL warehouses,
  pipelines, UC objects, workspaces), `databricks.yml` bundles, and
  Python sdk/dbutils/notebook evidence.
- `analyzers/delta_model.py` — `DeltaProjectModel` over SQL
  (`USING DELTA`, MERGE/UPDATE/DELETE/OPTIMIZE/VACUUM/RESTORE/CLUSTER BY,
  TBLPROPERTIES), Python `DeltaTable` bound-receiver method ops,
  `.format("delta")` reads/writes, and streaming delta endpoints;
  features (deletion vectors, CDF, liquid clustering, column mapping,
  schema evolution, identity columns) and protocol floors.
- `checks/platforms.py` — EMR000-007, DBX000-006, DELTA000-004,
  registered in the checks registry.
- `checks/platform_rules.py` — PLAT008 (EMR Iceberg row-level writes +
  LF grants + no `security_configuration`) and PLAT009 (DBR below the
  protocol floor of a detected Delta feature, capability-gated).
- `platform_graph_builder` — `_TF_TYPED` gains EMR/Databricks resource
  types; new `_platforms` adapter emits `compute_job:emr|databricks`,
  UC catalog/location/principal entities, `table:delta` entities, and
  per-op QUERY→table WRITES/DEPENDS_ON edges.
- `cli/platforms.py` — `emr|databricks|delta inspect|findings` +
  `delta features`.
- Knowledge packs: `capabilities/{emr,databricks,delta}.json`,
  `emr/releases.json`, `databricks/runtime.json`, `delta/features.json`.
- Tests: `tests/unit/test_platforms.py` (11), `tests/unit/adversarial/
  test_platforms.py` (9) — model extraction, checks, graph entities,
  cross-domain rules, FP resistance, determinism.

## Fixes folded into this phase

- `hcl_lite` attr regex treated `//` inside quoted strings as a comment
  (truncated `log_uri = "s3://..."`); comment stripping is now
  quote-aware.
- `aws_emrserverless_application` (real provider type) plus the
  `aws_emr_serverless_application` spelling both accepted.
- `EmrEksCluster` gained `release: str = ""` for a uniform
  version-check interface (virtual clusters pin no release).
- Delta CDF token list gained `enablechangedatafeed` (the actual
  property name).
- Delta model now binds `x = DeltaTable.forName(...)` receivers so
  `x.update()`/`x.optimize()` count as table ops; SQL ops carry their
  target table for graph edges.

## Verification

- `pytest tests` — 1091 passed
- `mypy src` — 127 files, clean
- `ruff check src tests` — clean; `ruff format --check` — clean
- `forge-doctor knowledge verify` — all packs ok
- CLI smoke on a multi-domain fixture rendered EMR clusters/serverless,
  Databricks job/cluster/UC objects, and Delta ops/features/protocol.

## Open questions / limits

- Delta `MERGE`/`UPDATE`/`DELETE` SQL targets are captured; Python
  bound-receiver ops don't yet record the target table (entity ids on
  ops only).
- EMR on EKS release labels live on job runs, not virtual clusters —
  out of scope for static IaC.
- Spec stays in `active/` pending human review (not archived).
