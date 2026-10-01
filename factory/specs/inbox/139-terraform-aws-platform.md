---
id: 139-terraform-aws-platform
title: Terraform stage 6 - AWS data platform (Glue/LF/EMR/S3/IAM) checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 6+13+14+15+27: the payoff domain. LF grants
vs data_lake_settings, Glue version/LF/Iceberg coherence vs CODE evidence,
EMR fleets, cross-account.
# Acceptance Criteria
- TFLF001 `aws_lakeformation_permissions` without
  `aws_lakeformation_data_lake_settings` evidence (WARNING - grant doesn't
  enforce by itself), TFLF003 grant without `aws_lakeformation_resource`
  registration for the location (INFO), TFLF006 cross-account principal
  mismatch.
- TFGLUE001 glue_version below pack floor, TFGLUE006 Iceberg job without
  datalake storage/props, cross-check with IcebergProjectModel writes.
- TFEMR001 release label floor, TFEMR003 no auto-termination on
  emrserverless, TFEMR005 spot on critical fleets.
- Cross-account: provider alias assume_role graph + RAM/resource-link legs.
- `knowledge/terraform/aws/{glue,lakeformation,emr}.json` schema 2.
