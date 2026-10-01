---
id: 130-emr-doctor
title: EMR Intelligence — EMRProjectModel, EMR### + EMRS### checks, `emr` CLI group
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_databricks_emr.md` phase 3. Four EMR products must stay
distinct: EMR on EC2, EMR Serverless, EMR on EKS, EMR Studio. Model fuses
IaC (`aws_emr_cluster`, `aws_emrserverless_application`, CFN
`AWS::EMR::*`), spark configs, bootstrap/steps refs.

# Acceptance Criteria
- `EMRProjectModel`: deployment kind (ec2/serverless/eks/studio),
  release_label, bundled Spark/Iceberg (from `knowledge/emr/releases.json`),
  instance fleets/groups, spot usage, dynamic allocation props, steps,
  runtime roles, LF integration flags.
- Checks: release label vs knowledge pack (EMR001 stale/EOL), Serverless
  capacity checks (EMRS001-006: release compat, memory/CPU ratio, dyn-alloc
  cap, max capacity, initial-capacity waste, idle timeout), EMR+Iceberg op
  compat via iceberg model (writes on releases below pack floor → WARNING),
  `emr inspect|compatibility|migrate --from --to` commands.
- `knowledge/emr/{releases,iceberg,lakeformation}.json` schema 2+sourced.

# Constraints
- Pack-driven compat; unknown release → HIGH "no bundled data".
