---
id: 189
title: Athena + Lambda + Step Functions Deep Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_serverless.py tests/unit/adversarial/test_serverless.py -x -q
  - python -m forge_doctor athena inspect <fixture>
  - python -m forge_doctor knowledge verify
---

# Phase 8 - Athena + Lambda + Serverless Deep Intelligence

## Context

Phase 8 of the ten-phase Forge Doctor program: complete serverless data
platform reasoning. Existing surface: `stepfunctions_model.py` (ASL parse,
SFN000-020 checks, `.sync`/`.waitForTaskToken` integration tags), runtime
adapters for Athena stats + Lambda REPORT (Phase 2), no Athena or Lambda
project models.

## Acceptance Criteria

- `AthenaProjectModel`: workgroups (engine version, enforced config,
  result location, scan cutoff, encryption), catalogs, databases,
  named/prepared queries, CTAS/UNLOAD/PREPARE SQL ops, Iceberg DDL,
  boto3 `athena` call-sites. Terraform + CloudFormation + SQL + code.
- `LambdaProjectModel`: functions (runtime, architectures, memory,
  timeout, ephemeral storage, reserved/provisioned concurrency, layers,
  VPC, DLQ, tracing, package type), event sources, destinations, layer
  versions, boto3 `lambda` calls, idempotency-library evidence.
- `StepFunctionsModel` deepening: `QueryLanguage` per machine and state,
  payload keys, retry MaxAttempts/ErrorEquals, Lambda `target`,
  Distributed Map MaxConcurrency/ToleratedFailurePercentage.
- Checks ATH000-005, LAM000-005, SFN030-032; deterministic; absent
  evidence never becomes a negative claim.
- Cross-domain rules: PLAT010 (SFN Lambda task + client-side Athena poll
  pair → native `.sync` candidate), PLAT011 (Distributed Map
  MaxConcurrency > invoked Lambda reserved concurrency).
- Platform graph: `compute_job:athena`, `query:athena`, `catalog:athena`,
  stream→lambda TRIGGERS edges, function→destination INVOKES edges.
- Knowledge packs: `capabilities/{athena,lambda}.json`,
  `athena/engines.json`, `lambda/runtimes.json`,
  `stepfunctions/query-languages.json`.
- CLI: `athena|lambda inspect|findings`; `stepfunctions inspect` surfaces
  query language, retry attempts, map detail, payload keys.
- Unit + adversarial tests; determinism; no FPs on substring names,
  generic SQL, or wrong-service boto3 clients.

## Constraints

- stdlib-first; no cloud/API calls; no LLM; no target-code execution.
- One functional commit:
  `feat(serverless): deepen Athena Lambda and Step Functions intelligence`.
- Do not archive on completion — human review gate.

## Review Notes

- Lambda function refs resolve on `function_name` attr, TF label, or CFN
  logical id (`LambdaFunction.tf_label` + `matches()`) - ref strings like
  `aws_lambda_function.fn.arn` never leak as entity ids.
- boto3 client bindings are arg-based AST walks (`client("athena")`),
  matching the emr/dynamodb convention.
