---
id: 009-trace
title: forge-doctor trace CHECK_ID FILE:LINE
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/integration/test_cli.py -q
---

# Acceptance Criteria
- re-runs the single check, locates the finding at file:line
- renders: matched evidence, enclosing symbol, receiver classification+confidence, assignment chain for receiver, supporting imports
- --json variant emits structured chain for agents
