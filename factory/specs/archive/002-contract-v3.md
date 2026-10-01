---
id: 002-contract-v3
title: Machine contract v3 (JSON/agent schema_version, no absolute path)
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_output.py tests/unit/test_finding_v2.py -q
---

# Context
JSON emits {"version": "0.3.0", "project": "/abs/path"} — conflates tool version
with schema and leaks filesystem layout, breaking cross-machine determinism.

# Acceptance Criteria
- JSON: {"tool": {"name","version"}, "schema_version": "3.0", "project": {"name": ...}, results:[...]}
- absolute root only emitted with explicit opt-in flag (scan --show-root)
- agent renderer gets same tool/schema_version block
- README example stays truthful
