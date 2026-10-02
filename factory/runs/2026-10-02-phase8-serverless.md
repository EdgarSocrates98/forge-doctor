# Run Record — Phase 8: Athena + Lambda + Serverless Deep Intelligence

- Spec: `factory/specs/active/189-serverless-deep-intelligence.md`
- Date: 2026-10-02
- Commit: `feat(serverless): deepen Athena Lambda and Step Functions intelligence`

## What was built

- `analyzers/athena_model.py` — `AthenaProjectModel` over Terraform
  (`aws_athena_workgroup`, `aws_athena_data_catalog`,
  `aws_athena_database`, `aws_athena_named_query`,
  `aws_athena_prepared_statement`), CloudFormation `AWS::Athena::*`,
  Athena-shaped SQL (CTAS/UNLOAD/PREPARE/EXECUTE, Iceberg DDL), and
  boto3 `athena` call-sites (AST arg-bound client vars).
- `analyzers/lambda_model.py` — `LambdaProjectModel` over Terraform
  (`aws_lambda_function`, event_source_mapping, permission,
  event_invoke_config, provisioned_concurrency, layer_version, S3/SNS/
  schedule triggers), CloudFormation `AWS::Lambda::*`, boto3 `lambda`
  calls, idempotency imports. `LambdaFunction.tf_label` + `matches()`
  resolve refs on name/label/logical-id deterministically.
- `stepfunctions_model.py` deepened — `SfnMachine.query_language`
  (JSONPath default vs JSONata), `SfnState.payload_keys` (InputPath,
  Parameters, ResultSelector, ResultPath, Arguments, Output, Assign,
  ItemSelector, ItemBatcher), `retry_max_attempts`, `retry_errors`,
  `catch_errors`, `target` (Parameters.FunctionName / arn tail),
  `max_concurrency`, `tolerated_failure`.
- `checks/serverless.py` — ATH000-005, LAM000-005, SFN030-032.
- `checks/platform_rules.py` — PLAT010 (SFN Lambda task + client-side
  Athena poll pair → `.sync` candidate, INFO) and PLAT011 (DISTRIBUTED
  Map MaxConcurrency > invoked Lambda reserved concurrency, WARNING).
- `platform_graph_builder` — `aws_athena_*` in `_TF_TYPED`; new
  `_serverless` adapter emits lambda/athena entities, stream→function
  TRIGGERS edges, and function→destination INVOKES edges.
- `cli/serverless.py` — `athena|lambda inspect|findings`;
  `stepfunctions inspect` shows query language, retry attempts, map
  concurrency/tolerance, payload keys, nested iterators.
- Packs: `capabilities/{athena,lambda}.json`, `athena/engines.json`,
  `lambda/runtimes.json`, `stepfunctions/query-languages.json`.
- Tests: `tests/unit/test_serverless.py` (10),
  `tests/unit/adversarial/test_serverless.py` (9).

## Fixes folded into this phase

- `_int()` in lambda_model no longer collapses an absent int attr to 0
  (reserved_concurrency sentinel -1 now survives).
- boto3 client binding for athena/lambda switched to AST arg inspection
  (`client("athena")`) — `module.assigns` lacks call args.
- TF ref strings (`aws_lambda_function.fn.arn`) resolve to the label for
  event sources, destinations, and provisioned-concurrency joins.

## Verification

- `pytest tests` — 1110 passed
- `mypy src` — 131 files, clean
- `ruff check` / `ruff format --check` — clean
- `forge-doctor knowledge verify` — all packs ok
- CLI smoke: `athena inspect`, `lambda inspect`, `stepfunctions inspect`
  render workgroup/catalog/queries, functions/triggers, and JSONPath +
  map detail; PLAT010/PLAT011 fire on the fixture.

## Limits

- Athena result-reuse/bytes-scanned runtime signals stay in the Phase-2
  runtime-evidence adapters (`runtime inspect`); the static model covers
  declared config only.
- PLAT011 only fires when the Map's ItemProcessor literally names a
  project-declared function (unknown stays unknown).
- Spec stays in `active/` pending human review.
