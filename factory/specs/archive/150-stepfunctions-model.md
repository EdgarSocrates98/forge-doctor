---
id: 150-stepfunctions-model
title: SFN stage 1 - StepFunctionsModel (ASL), SFN0## checks, `stepfunctions` CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_stepfunctions.py tests/unit/test_stepfunctions_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 1 of 9 (AWS Serverless Data
Intelligence). ASL definitions are JSON - stdlib-parseable. Sources:
`*.asl.json`/`*.states.json`, any JSON with StartAt+States, Terraform
`aws_sfn_state_machine` `definition`/`definition_string` quoted JSON,
CFN `AWS::StepFunctions::StateMachine` DefinitionString.

# Acceptance Criteria
- `analyzers/stepfunctions_model.py` `StepFunctionsModel`: machines with
  name/file/line, type (STANDARD|EXPRESS|unknown), states (name/type/
  next/end/choices+default/retry/catch/resource-arn/timeout/heartbeat/
  map-mode/iterator), integration family from Resource arn
  (lambda/glue/athena/ecs/sns/sqs/dynamodb/sdk:*), edges.
- `checks/stepfunctions.py` (`category = "stepfunctions"`): SFN000 anchor,
  SFN002 unreachable state, SFN003 dead-end task (no Next/End, WARNING),
  SFN005 Choice without Default (INFO), SFN010 Task .sync/waitForTaskToken
  without TimeoutSeconds (INFO), SFN020 Distributed Map (`"ItemProcessor"`
  or `"ItemReader"` + `ProcessorMode "DISTRIBUTED"`) inside an EXPRESS
  machine (WARNING). why/when_ok/fix each.
- `cli/stepfunctions.py`: `forge-doctor-data stepfunctions inspect [path]` -
  machines, type, states by type, integrations, retry/catch presence,
  risks (severity-sorted).
- `knowledge/stepfunctions/integrations.json` - service-integration arn
  map + notes (.sync support, callback support), schema 2 + sources.
- Tests per check + CLI; docs checks.md SFN rows + CHANGELOG.

# Constraints
- State refs inside Map `Iterator`/`ItemProcessor` bodies count as nested
  graphs for reachability (walk them).
- Graph claims must be evidence-based: unreachable determined from StartAt
  traversal over Next/Choices/Default/inner graphs only.
