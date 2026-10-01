---
id: 135-terraform-modules-providers
title: Terraform stage 2 - module/provider intelligence (aliases, pinning, lockfile)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_terraform.md` stages 2+24: provider configurations belong in the
root module; `.terraform.lock.hcl` is first-class.
# Acceptance Criteria
- TF004 provider block inside a child module dir (modules/ or referenced
  local module path), TF005 child module missing explicit `providers`
  wiring for aliased providers, TF006 provider alias referenced but never
  declared, TF007 cross-account provider pair (same provider, different
  assume_role) without explicit alias usage in resources.
- `.terraform.lock.hcl` parsed: TF120 lock missing when providers pinned,
  TF121 lock version outside required_providers constraint,
  TF124 duplicate provider entries in lock.
- `knowledge/terraform/providers.json` extended with major-version floors.
