---
id: 156-athena-cost-compat
title: Athena stage 2 - cost signals, result reuse, CTAS/UNLOAD, Iceberg/LF cross-checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_lambda_step_athena.md` sub-cycle 7 (sections 24-35).
# Acceptance Criteria
- `athena cost` command: static cost signals per query (scan width,
  partition pruning absence, format) - labeled static risk.
- ATH040 repeated identical query strings (result-reuse cand
