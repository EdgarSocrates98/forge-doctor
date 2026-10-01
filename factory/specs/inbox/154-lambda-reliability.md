---
id: 154-lambda-reliability
title: Lambda stage 2 - concurrency/idempotency/event-source checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 5 (sections 16-20, 30).
# Acceptance Criteria
- LAMBDA050 event-source mapping without concurrency cap on shared
  function, LAMBDA051 reserved concurrency absent where SFN Map calls it,
  LAMBDA060 side-effecting handler (boto3 put/write calls) with retryable
  trigger and no idempotency evidence, LAMBDA070 SQS visibility_timeout
  < lambda timeout, LAMBDA072 partial-batch response absent on SQS/Kinesis
  mapping, LAMBDA074 no DLQ/destination on async trigger.
- Cross-model edges: SFN Distributed Map MaxConcurrency vs Lambda
  reserved concurrency (SFN->LAMBDA corr).
