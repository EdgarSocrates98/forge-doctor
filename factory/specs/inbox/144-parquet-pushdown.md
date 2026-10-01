---
id: 144-parquet-pushdown
title: Parquet stage 3 - statistics/pushdown reasoning cross-SQL
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle 3: cross Parquet metadata with
SqlIndex - non-sargable predicates on parquet-backed tables reduce
pruning; filter-heavy columns vs statistics/bloom-filter availability.
# Acceptance Criteria
- PARQ030 filter column lacks statistics (needs stage-2 metadata or
  explicit writer options), PARQ033 stats ineffective (non-sargable SQL
  on a parquet-dataset column), PARQ034 expression blocks pushdown.
- Evidence-gated: without metadata snapshot, only code-side signals fire.
