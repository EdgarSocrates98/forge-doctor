---
id: 105-spark-crossfile-gate
title: Spark checks use cross-module evidence — not just uses_pyspark
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_spark_ast.py tests/unit/test_index.py -q
  - python -m pytest -x -q
---

# Context
checks/spark.py skips modules where `not module.uses_pyspark` — a file that
receives a DataFrame from an imported producer (no pyspark import itself) is
skipped entirely, a false negative the semantic index was built to catch.

# Acceptance Criteria
- Gate becomes: analyze when `uses_pyspark OR df_names non-empty OR module
  has cross-module spark evidence` (producer-resolved df names).
- Regression test: reader.py exports load_orders(spark) -> parquet; job.py
  does `df = load_orders(spark); df.collect()` with NO pyspark import →
  SPARK001 fires on job.py:collect.
- No new false positives on plain non-spark files.
- Index internals: `for _ in range(6)` → `while changed` bounded by symbol
  count; functions keyed by qualified name (Class.method) to avoid
  collisions between same-named methods.

# Constraints
- Deterministic, offline. Confidence stays honest (cross-file = medium when
  receiver inferred, not imported).
