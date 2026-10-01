---
id: 134-terraform-model
title: Terraform stage 1 - TerraformProjectModel, TF0## checks, `terraform` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_terraform.py tests/unit/test_terraform_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_terraform.md` stage 1 of 8: Terraform becomes a semantic domain
(the "infrastructure ownership" bridge to Glue/LF/EMR/Databricks). Extends
`hcl_lite` (dependency-free core); a full-HCL parser is an optional extra
later. Existing `iac` checks stay untouched.

# Acceptance Criteria
- `analyzers/terraform_model.py` `TerraformProjectModel` over `*.tf`:
  terraform block (required_version, required_providers, backend type),
  provider blocks (name/alias/region), module blocks (source/version),
  resources (reuse hcl_lite flat attrs), data/variable/output/locals/moved/
  import/check block presence, and reference edges
  (`<type>.<name>` / `module.x.y` / `var.z` / `local.w` inside attr values).
  Memoized on ctx; deterministic.
- `checks/terraform.py` (`category = "terraform"`): TF000 anchor, TF001
  resources exist but no `required_version`, TF002 provider without version
  constraint, TF003 overly broad constraint (`>=`/`~>` without upper bound),
  TF020 local module detected (INFO), TF021 registry module unpinned
  (WARNING), TF022 git module without immutable ref (`ref=main|master|HEAD`
  or no ref - WARNING), TF130 `backend "local"` (WARNING). why/when_ok/fix
  on each.
- `cli/terraform.py`: `forge-doctor terraform inspect [path]` rendering the
  reviewer's shape (Terraform version / Providers / Resources by domain /
  Modules / State / Findings).
- `knowledge/terraform/language.json` (feature floors: moved>=1.1,
  check>=1.5, terraform test>=1.6, import blocks>=1.5) + `providers.json`
  (source map aws->hashicorp/aws, databricks->databricks/databricks),
  schema_version 2 + sources.
- Tests: model facts per block type + reference edges, each check
  positive+negative, CliRunner on inspect. Docs: checks.md TF rows,
  CHANGELOG.

# Constraints
- hcl_lite remains the zero-dep parser; no new runtime deps. Deep HCL
  expressions that can't resolve statically stay unknown - never guessed.
