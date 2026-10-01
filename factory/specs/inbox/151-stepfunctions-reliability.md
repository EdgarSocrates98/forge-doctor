---
id: 151-stepfunctions-reliability
title: SFN stage 2 - retry/catch policies, timeouts, integration patterns
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 2 (SFN040-055, SFN021-022).
# Acceptance Criteria
- SFN007 Retry on Task without Catch, SFN040 retry entry ErrorEquals
  covering non-retryable families per pack, SFN042 retry missing
  BackoffRate, SFN044 Athena-family integration polled via custom
  Wait+GetQueryExecution loop while `.sync` supported, SFN046 catch-all
  ErrorEquals ["States.ALL"] before specific catches shadowing them,
  SFN051 manual poll loop despite .sync support, SFN021 EXPRESS machine
  with Wait/poll loops or >5min-timeout tasks (INFO).
- Retry-policy evidence normalized in model (attempts/backoff/interval).
