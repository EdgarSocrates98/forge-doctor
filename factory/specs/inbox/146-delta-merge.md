---
id: 146-delta-merge
title: Delta stage 2 - MERGE/DML reconstruction and checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle 5: Delta MERGE Doctor - reuse the
SqlIndex merge fields from spec 129; DeltaTable.merge() Python API paths.
# Acceptance Criteria
- DELTA020 merge predicate too broad (ON cols never touch partition cols),
  DELTA024 merge used for append-only workload (matched-clause shape),
  DeltaTable.merge(...) calls reconstructed in `delta merge` command.
- Cross-checks: merge on delta table + repartition(1) source hint.
