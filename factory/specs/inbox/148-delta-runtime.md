---
id: 148-delta-runtime
title: Delta stage 4 - runtime compatibility (Glue bundled Delta, Databricks)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle 7 + #25-27: Glue bundles fixed
Delta versions (3.0->1.0.0, 4.0->2.1.0, 5.0->3.3.0, 5.1->3.3.2);
`--datalake-formats delta` requirement; Databricks managed vs OSS Delta.
# Acceptance Criteria
- `knowledge/delta/glue.json` bundled-version matrix + feature floors.
- GLUEDELTA001 delta code without `--datalake-formats=delta` in Glue IaC/
  conf (WARNING), GLUEDELTA002 missing spark extensions/catalog, DELTA
  feature-vs-runtime check (deletionVectors need Delta>=3.0 per protocol).
- `delta compatibility` command rendering runtime -> bundled -> features.
