---
id: 015-migrate
title: forge-doctor migrate — project-aware migration report
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_migrate.py -q
---

# Acceptance Criteria
- `forge-doctor migrate glue --from 4.0 --to 6.0 [path]`
- combines knowledge changes + real signals: glue_version pins (code+IaC), DynamicFrame usage, pyspark dep, python requires, iceberg
- grouped report: Runtime / Code / Dependencies / Infrastructure, each HIGH|MEDIUM|INFO w/ file:line
- knowledge pack extended with optional code_signals; --format json
