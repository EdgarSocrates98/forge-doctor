---
id: 147-delta-maintenance
title: Delta stage 3 - OPTIMIZE/VACUUM/layout (ZORDER, liquid clustering)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle 6 + #16-19: managed optimization
awareness (predictive optimization suppresses "no OPTIMIZE").
# Acceptance Criteria
- DELTA030 small-file risk (parquet stats + delta writes), DELTA031
  OPTIMIZE absent and no managed-optimization evidence, DELTA050 VACUUM
  absent, DELTA051 retention dangerously low (`delta.logRetentionDuration`
  / vacuum calls with tiny hours), DELTA040 partitioning + CLUSTER BY
  liquid-clustering conflict, DELTA041 ZORDER + liquid conflict.
- `delta maintenance` command; managed-optimization evidence detected
  (catalog_type unity / predictive-optimization props) suppresses 031.
