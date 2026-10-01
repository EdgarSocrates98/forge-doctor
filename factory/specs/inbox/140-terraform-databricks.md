---
id: 140-terraform-databricks
title: Terraform stage 7 - Databricks provider resources + ownership collisions
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 7+16+17: databricks/* resources, UC grants,
and "who owns the resource" (Terraform vs Asset Bundles vs CLI scripts).
# Acceptance Criteria
- TFDBX001 databricks provider detected, TFDBX005 `databricks_grants` with
  ALL_PRIVILEGES / wildcard catalog, TFDBX008 same resource managed by both
  Terraform and a databricks.yml bundle (name match heuristic, WARNING).
- Ownership evidence kind in model;
