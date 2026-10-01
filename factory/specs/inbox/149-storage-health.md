---
id: 149-storage-health
title: Storage stage 8 - `storage health|migrate`, cross-format intelligence
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle
