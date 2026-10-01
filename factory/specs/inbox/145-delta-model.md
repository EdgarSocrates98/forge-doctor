---
id: 145-delta-model
title: Delta stage 1 - DeltaTableModel, protocol/table-features, DELTA0## anchor checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---
# Context
`prompt_evo_parquet_delta.md` sub-cycle 4: Delta = transaction log over
Parquet. Model code evidence (`format("delta")`, DeltaTable API,
OPTIMIZE/VACUUM/MERGE calls), `_delta_log` dirs (JSON commit files
parsed - they're plain JSON), catalog hints, table features.
# Acceptance Criteria
- `DeltaTableModel`: writers/readers, `delta.tables` evidence, table
  properties (delta.* keys), `_delta_log` presence + latest protocol
  (reader/writer version, table features) when a log dir ships in repo,
  DeltaTable.forPath/forName calls, MERGE SQL on delta tables.
- DELTA000 anchor, DELTA001 delta usage without log/catalog evidence,
  DELTA002 reader/writer protocol vs pack floor.
- `delta inspect` command; `knowledge/delta/{versions,protocol,
  table-features}.json` (protocol versions, feature floors incl.
  deletionVectors/columnMapping).
