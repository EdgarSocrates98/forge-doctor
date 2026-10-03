---
id: 007-semantic-index
title: Semantic Analysis Index + cross-file DataFrame propagation
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_spark.py tests/unit/checks/test_glue.py -q
---

# Acceptance Criteria
- forge_doctor_data/analyzers/index.py: per-file imports, calls(name,receiver,line,scope), assignments, symbols(fn/class ranges), df-producer functions
- one ast.parse per .py file, shared by spark+glue analyzers (memoized on ctx)
- cross-file: `from reader import load_orders` + `df = load_orders(spark)` marks df when load_orders returns spark-derived expr
- spark findings keep existing precision (no regressions in tests)
