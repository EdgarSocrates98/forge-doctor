---
id: 133-lakeformation-deep
title: Lake Formation stage 2 — FGAC/FTA matrix, cross-account share graph, `lakeformation` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_databricks_emr.md` phase 2 expansion over spec 114's base.
LF010-015 FGAC/FTA mix on Glue 5+, LF020-028 cross-account share graph
(RAM/resource-link/grant legs), LF030-034 LF-TBAC tag checks, LF040-045
hybrid-access checks, permission graph view.

# Acceptance Criteria
- `LakeFormationModel` (builds on 114's checks): principals/grants/
  registered locations/resource links/LF-Tags/hybrid flags from IaC.
- FGAC×FTA check: job/job-config evidence mixing both → WARNING;
  FGAC-enabled + Iceberg write op → WARNING (authorization path differs —
  the reviewer's LF042 example shape: operation × runtime × access model).
- Cross-account legs: RAM share + resource link + grant evidence; missing
  leg → finding naming the leg.
- `forge-doctor lakeformation inspect|permissions|graph` commands.
- `knowledge/lakeformation/{permissions,access-models,compatibility}.json`
  schema 2 + sources.

# Constraints
- Evidence-gated correlations only; "possible" wording for inference.
