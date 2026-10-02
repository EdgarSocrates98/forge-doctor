---
id: 114-lakeformation-doctor
title: Lake Formation pack + diagnose correlation for credential-vending errors
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_diagnose.py tests/unit/checks/test_lakeformation.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Priority #3 from `prompt_evo_novas_evolucoes.md`. Lake Formation failures are
"unexplainable" errors — the pack + diagnose correlation is the point.
Reviewer's example: `GetTemporaryCredentialsForTableV2` + Glue 5.x + FGAC +
write op → credential-vending/write-path conflict hint.

# Acceptance Criteria
- Knowledge pack `src/forge_doctor/knowledge/lakeformation/` covering the
  reviewer list: GetTemporaryCredentialsForTable(V2), credential vending,
  FGAC, resource links, RAM/cross-account, hybrid access,
  IAMAllowedPrincipals, LF-tags, DATA_LOCATION_ACCESS, register/S3 path
  requirements — each entry with signature patterns + cause + fix hints +
  provenance (schema_version 2).
- `diagnose`/`trace` maps at least the credential-vending error families
  (AccessDenied on GetTemporaryCredentials*, "Insufficient Lake Formation
  permission", registered-location S3 deny) to pack entries with fix hints;
  when repo evidence shows Glue ≥5.x + `lakeformation`/`fgac` config + a
  write operation in code, the diagnosis adds a correlation line naming the
  possible vending/write conflict.
- `checks/lakeformation.py` (`category = "lakeformation"`): LF### static
  checks on IaC + code facts only where honest — e.g. `LF001` resource link
  target + no RAM/share evidence; `LF002` IAMAllowedPrincipals grant
  alongside FGAC tags (hybrid-access ambiguity). Each with why/when_ok/fix.
- Tests: diagnose on a log fixture containing a vending denial returns the
  pack entry; correlation line appears only when all three evidence legs
  present; LF checks on CFN/HCL fixtures; deterministic.

# Constraints
- Correlation must be evidence-gated: no evidence leg → no correlation claim
  (avoids over-promising). Honest "possible" wording.
- Knowledge entries stay offline/static — no AWS API calls.

# Review Notes
- Depends on nothing in 112/113 but composes with them later (Iceberg on
  Glue + LF is the flagship cross-domain finding).
