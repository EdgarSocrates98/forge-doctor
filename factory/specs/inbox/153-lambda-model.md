---
id: 153-lambda-model
title: Lambda stage 1 - LambdaProjectModel, LAMBDA0## checks, `lambda` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 4 (sections 10-12, 20).
Sources: Terraform `aws_lambda_function`, CFN `AWS::Lambda::Function`,
SAM `AWS::Serverless::Function`, serverless.yml, handler detection in
`*.py` (`def handler(`/`def lambda_handler(`).
# Acceptance Criteria
- `LambdaProjectModel`: functions (name, runtime, memory, timeout,
  ephemeral_storage, layers, reserved/provisioned concurrency, vpc,
  dlq/destination, event sources, handler path, env var keys) from IaC +
  source-level handler evidence; runtimes vs
  `knowledge/lambda/runtimes.json` deprecation pack.
- LAMBDA000 anchor, LAMBDA002 deprecated/EOL runtime (WARNING),
  LAMBDA011 memory >= pack oversized threshold on handler with no
  heavy-signal imports (pandas/numpy/pyarrow) (INFO), LAMBDA020 timeout
  vs detected caller (SFN task) mismatch, LAMBDA030 ephemeral storage
  small while /tmp usage detected in code (INFO), LAMBDA090 VPC config
  with no private-resource evidence (INFO).
- `forge-doctor lambda inspect` command.
