---
id: 137-terraform-plan
title: Terraform stage 4 - plan JSON analysis, blast-radius, plan-diff
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 4+18+19+20+31: `terraform show -json` is
user-provided input (never run terraform); blast radius via model graph.
# Acceptance Criteria
- `forge-doctor-data terraform plan plan.json` - create/update/replace/delete
  counts + critical-change findings: TF100 destructive replace, TF101
  unexpected delete, TF102 mass replacement, TF103 IAM privilege expansion
  (policy actions widened), TF104 LF grant expansion, TF106 critical
  resource recreation (stateful types from pack).
- `forge-doctor-data terraform blast-radius <addr>` - reverse dep walk of the
  model graph.
- `forge-doctor-data terraform plan-diff before.json after.json`.
- `knowledge/terraform/state.json` - critical/stateful resource types.
