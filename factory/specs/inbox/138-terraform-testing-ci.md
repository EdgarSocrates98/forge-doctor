---
id: 138-terraform-testing-ci
title: Terraform stage 5 - terraform test/check blocks + CI workflow checks
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 5+21+23.
# Acceptance Criteria
- TF110 resources but no `*.tftest.hcl` anywhere (INFO), TF111 variable
  with no `validation` block where a knowledge-pack domain exists
  (glue_version etc.), TF112 resource without `lifecycle` precondition
  where pack marks critical, check-block detection in model.
- CI scan of workflows: TFCI001 apply without plan step, TFCI005
  `-auto-approve`, TFCI007 fmt/validate absent, TFCI008 test absent.
