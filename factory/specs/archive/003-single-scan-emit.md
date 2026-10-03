---
id: 003-single-scan-emit
title: Single-scan multi-emit + fixed action.yml SARIF upload
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/integration/test_cli.py -q
  - python -m forge_doctor_data scan tests/sample_projects/spark_problem_project --emit text --emit json:/tmp/fd.json
---

# Context
action.yml scans twice; if --fail-on exits 1, SARIF never uploads.
Rule: analysis happens once; rendering happens N times.

# Acceptance Criteria
- --emit FMT[:PATH] repeatable on scan + category commands; FORMAT in text|json|html|sarif|agent
- --format/--output still work (single-emit shorthand)
- action.yml: one scan step (text stdout + sarif file), upload uses if:always(), exit code enforced in final step
- conflicting duplicate stdout emits handled sanely
