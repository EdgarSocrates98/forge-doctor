# Run: 150-stepfunctions-model (Step Functions stage 1)

- Spec: `factory/specs/active/150-stepfunctions-model.md` (staged, grill: completed)
- Agent: devin
- Source prompt: `prompt_evo_lambda_step_athena.md` — 9-subcycle AWS
  Serverless Data Intelligence program (Step Functions model → SFN
  reliability/integrations → dataflow/Distributed Map → Lambda model →
  Lambda reliability → Athena model → Athena cost/compat → cross-domain
  graph → runtime/diagnose/cost).

## Implemented

- `StepFunctionsModel` (`analyzers/stepfunctions_model.py`) — stdlib-only
  ASL parsing, zero dependencies. Sources: `*.asl.json`/`*.states.json`/
  `*.sfn.json`, any `*.json` carrying `StartAt`+`States`, Terraform
  `aws_sfn_state_machine` blocks (per-resource body scan; quoted or
  heredoc `definition`), CFN `AWS::StepFunctions::StateMachine`
  `DefinitionString`. `SfnState` exposes name/type/next/end/default/
  choices/catches/resource/integration/timeout/heartbeat/retry+catch
  counts/map-mode; Map `Iterator`/`ItemProcessor` bodies model as nested
  `SfnMachine`s so reachability walks them. Integration family derived
  from Resource arn (`lambda`, `glue:sync`, `athena:sync`, `sdk:*`,
  `*:callback` for `.waitForTaskToken`).
- 6 checks (`category = "stepfunctions"`): SFN000 anchor, SFN002
  unreachable state (StartAt BFS over Next/Choices/Default/Catch),
  SFN003 dead-end path, SFN005 Choice without Default, SFN010
  sync/callback task without TimeoutSeconds, SFN020 Distributed Map
  inside an EXPRESS machine.
- `forge-doctor stepfunctions inspect` — per-machine type/state-type
  counts/integrations/retry+catch inventory, IaC refs, severity-sorted
  risks. Verified live.
- `knowledge/stepfunctions/integrations.json` — service-integration arn
  map with sync/callback support flags + notes, schema 2 + sources.
- Inbox specs for sub-cycles 2–9: 151 reliability/integrations, 152
  dataflow/Distributed Map/payload, 153 Lambda model, 154 Lambda
  reliability/concurrency/events, 155 Athena model, 156 Athena
  cost/compat, 157 cross-domain serverless graph, 158 runtime/diagnose/
  cost.

## Latent quirks found

- Terraform heredoc regex requires `\n` before the terminator —
  `<<EOT\n{...}EOT` (no newline) silently yields no machine. Test
  fixtures must emit `}\nEOT`.
- IaC extraction must brace-scope each `aws_sfn_state_machine` resource —
  searching the whole `.tf` file for `"StartAt"` mis-attributes
  definitions when multiple resources share a file.
- Multi-line `why = "…" "…"` splits into a dead second statement when
  the parens are missing — ruff format catches it only as unformatted,
  not as a bug.

## Verification

- `pytest -q` (targeted): 16 passed
- `pytest -x -q`: 610 passed (+16)
- `ruff check`, `ruff format --check`: clean
- `mypy src`: clean (86 files)
- E2E: `stepfunctions inspect` on fixture renders machine type, state
  counts, `glue:sync`/`lambda`/`sns` integrations, retry/catch blocks,
  IaC ref `pipe`, and SFN002/SFN010/SFN000 findings.

## Gate

Spec left in `active/` — awaiting human review + `archive --accepted`.
