---
id: 152-stepfunctions-dataflow
title: SFN stage 3 - Distributed Map, payload/IO, JSONata/JSONPath, cost signals
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 3 (SFN030-035, SFN060-072,
cost section 9).
# Acceptance Criteria
- SFN030 inline Map iterating an unbounded source (ItemsPath $ /
  S3 manifest absent) where pack flags high fanout risk, SFN032
  Distributed Map without tolerated-failure threshold, SFN035 distributed
  Map without ResultWriter, SFN060 `$` payload passed through
  ResultPath/OutputPath unchanged across >N states (INFO heuristic),
  SFN070 QueryLanguage mixed within one machine.
- `forge-doctor stepfunctions cost` - static cost signals (state count,
  transitions, Map fanout, polling loops) labeled static risk.
