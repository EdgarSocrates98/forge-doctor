---
id: 178-neptune-model
title: Connected Data phase F1 - NeptuneProjectModel + infra/ingest checks + neptune CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_neptune.py tests/unit/test_neptune_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase F, part 1. Neptune = managed
graph runtime for the graph concepts from 174. Model sources: Terraform
(`aws_neptune_cluster`, `aws_neptune_cluster_instance`, subnet/SG/
parameter groups, IAM), client code (endpoint/port/SSL params), bulk-
loader call sites, and the query-language files the analyzers in 179
will parse.

# Acceptance Criteria
- `analyzers/neptune_model.py` `NeptuneProjectModel`: cluster/instances/
  replicas, engine version, IAM auth flag, networking (subnet groups,
  security groups, public-access signals), endpoints observed in code,
  detected query languages (gremlin|opencypher|sparql by file/API
  evidence), bulk-loader usage, backup/PITR config.
- `checks/neptune.py` (`category = "neptune"`): NEP001 anchor; NEP030
  row-by-row large ingestion (write-per-item loop over graph API);
  NEP031 bulk-loader candidate; NEP032 bulk-loader S3 role mismatch;
  NEP033 malformed graph input risk; NEP040 no read replica on
  read-heavy workload (only when read/write asymmetry is observable —
  otherwise INFO-gated); NEP041 weak backup/PITR; NEP042 public-access
  assumption; NEP043 IAM auth inconsistent between cluster and client;
  NEP044 security-group topology risk; NEP045 cluster/instance config
  mismatch. why/when_ok/fix each.
- `cli/neptune.py`: `neptune inspect .`, `neptune ingest .`,
  `neptune compatibility .` (via 173 registry where applicable).
- `knowledge/neptune/`: `engines.json`, `ingestion.json`,
  `features.json`, `compatibility.json` (schema 2 + sources).
- Tests per check + model + CLI; registration; docs + CHANGELOG.
- `tests/unit/adversarial/test_neptune.py` — Definition of Done: every
  new semantic model ships adversarial fixtures (FP/FN/malformed).

# Constraints
- Infra checks consume the existing Terraform model where possible —
  don't re-parse `.tf`.
- NEP040-style capacity claims require observed evidence; default to
  honest "not observable statically" wording.
