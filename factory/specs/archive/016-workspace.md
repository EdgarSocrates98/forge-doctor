---
id: 016-workspace
title: Workspace orchestration — scan/diff over sub-projects
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/integration/test_cli.py -q
---

# Acceptance Criteria
- `forge-doctor workspace` keeps discovery (group, invoke_without_command)
- `workspace scan` runs scan per nested project, aggregates w/ project column; --format json|sarif|text; --profile applies per project
- `workspace diff base...head` per-subproject finding diff (project-prefixed)
