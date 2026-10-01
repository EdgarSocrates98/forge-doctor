---
id: 143-parquet-physical
title: Parquet stage 2 - physical health via footer snapshot / optional extra
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycles 2+3+35: row groups, encodings,
statistics, bloom filters - requires real metadata. Two input paths:
`--metadata parquet-metadata.json` (user-exported snapshot, like
pyarrow/fastparquet dump) and optional `[parquet]` extra (pyarrow footer
read when files are local). Never reads data rows.
# Acceptance Criteria
- `parquet inspect --metadata FILE` ingests a snapshot JSON schema
  (files/row_groups/compression/encodings/stats presence) into the model.
- Optional pyarrow path gated on import - absent = code-only behavior.
- PARQ010-013 row-group checks (tiny/huge groups, 1-group tiny files,
  inconsistent sizes), PARQ020-021 codec checks on real metadata,
  PARQ030-032 statistics/pushdown gaps.
- `knowledge/parquet/{format,compression}.json` enriched.
