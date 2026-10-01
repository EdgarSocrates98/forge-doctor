---
id: 131-databricks-doctor
title: Databricks Intelligence — DatabricksModel, DBX### checks, `databricks` CLI group
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_databricks_emr.md` phase 4. Databricks is more than Spark: jobs
(bundle JSON / workspace conf), clusters, Unity Catalog, Delta vs Managed/
Foreign Iceberg, runtimes.

# Acceptance Criteria
- `DatabricksModel`: databricks.yml/asset-bundle defs, cluster/job specs
  (new_cluster.spark_version, runtime pins), notebook markers,
  Unity Catalog refs (catalog.schema.table 3-level names, hive_metastore
  usage), DBFS paths (`/dbfs`, `dbfs:/`), Delta markers (`delta.` format,
  DeltaTable), Iceberg markers.
- Checks: DBX001 anchor, DBX002 legacy hive_metastore dependency, DBX003
  dbfs path usage, DBX004 unqualified table name (no catalog), DBX010
  runtime pin vs knowledge pack, DBX020+ foreign-Iceberg write attempt
  (writeTo on table cataloged as foreign/unity per pack hints).
- `databricks inspect|migrate --from X --to Y` commands.
- `knowledge/databricks/{runtimes,iceberg,unity-catalog}.json` schema 2.

# Constraints
- Foreign-vs-managed Iceberg distinction is pack-driven; unknown → INFO
  "ownership ambiguous", never assume writable.
