---
id: 158-serverless-diagnose
title: Serverless stage 9 - error packs + runtime snapshots + cost roll-up
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 9 (sections 41-42):
diagnose packs + user-provided snapshots.

# Acceptance Criteria
- `knowledge/errors/{stepfunctions,lambda,athena}.json` wired into
  `diagnose` (States.Timeout/TaskFailed, Lambda.TooManyRequests/
  Sandbox.Timedout/Runtime.ExitError, Athena HIVE_BAD_DATA/
  COLUMN_NOT_FOUND/ICEBERG_*).
- `aws-serverless inspect --stepfunctions sfn.json --lambda l.json
  --athena a.json` snapshot ingestion (user-exported, never calls AWS).
