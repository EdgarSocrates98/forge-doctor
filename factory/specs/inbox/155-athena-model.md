---
id: 155-athena-model
title: Athena stage 1 - AthenaProjectModel (workgroups, queries), ATH0## checks, `athena` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 6 (sections 21-23, 26).
Sources: IaC `aws_athena_workgroup`, SqlIndex statements (Athena dialect),
code (`boto3.client("athena")`, `start_query_execution`), configs.
# Acceptance Criteria
- `AthenaProjectModel`: workgroups (engine version, output location,
  encryption, override flag), query sites, CTAS/INSERT/UNLOAD/MERGE
  statements, prepared-statement calls, catalogs/databases.
- ATH000 anchor, ATH002 engine version implicit (no workgroup pin, INFO),
  ATH010 SELECT * on athena evidence (reuse SQL001 semantics, INFO),
  ATH011 query on partitioned-evidence table lacking partition predicate
  (INFO), ATH030 query runs in primary workgroup unintentionally (INFO),
  ATH031 no output location configured (WARNING), ATH033
  enforce_workgroup_configuration false (INFO).
- `forge-doctor athena inspect`.
