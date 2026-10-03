---
id: 005-policy-as-code
title: Policy as Code — custom policies, governed suppressions
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_policy.py tests/unit/test_config.py -q
---

# Acceptance Criteria
- [tool.forge-doctor-data.policy] extends = "<builtin>"; [tool.forge-doctor-data.policy.rules.ID] severity="error"|enabled=false
- [[tool.forge-doctor-data.suppressions]] rule=ID path=<posix-glob> reason=... owner=... expires=YYYY-MM-DD
- suppressions remove matching results; expired suppressions reactivate + emit POLICY001 WARNING
- `forge-doctor-data suppressions` lists ACTIVE/EXPIRED/UNUSED
- JSON report gains "suppressed" count + suppression detail
