---
id: 012-lineage
title: Static lineage — forge-doctor-data lineage
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/test_lineage.py -q
---

# Acceptance Criteria
- detects spark.read.table/parquet/csv/json/orc, read.format().load(), spark.sql (FROM/JOIN/INSERT via stdlib SQL tokenizer), writeTo, saveAsTable, insertInto, write.parquet/save(path), glue create_dynamic_frame.from_catalog(database/table_name), boto3 athena? (skip)
- graph: dataset nodes + job(module) nodes, edges reads/writes
- --format text|json|dot|mermaid|openlineage; openlineage emits run/job/inputs/outputs-shaped JSON
