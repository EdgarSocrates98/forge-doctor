---
id: 136-terraform-state-refactor
title: Terraform stage 3 - state/refactoring/backend checks (moved/import/removed)
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 3+25+26: moved/import/removed blocks,
backend, workspaces.
# Acceptance Criteria
- TF130 backend "local" (from 134), TF131 no backend block at all in a
  project with resources (INFO), TF132 s3 backend without
  `dynamodb_table`/`use_lockfile` (state locking absent, WARNING),
  TF133 backend block containing credential-looking attrs (WARNING),
  TF140 heavy `terraform.workspace` usage (>N refs across files, INFO).
- moved/import/removed blocks counted in model + inspect output.
