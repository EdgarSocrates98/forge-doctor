---
id: 011-spark-runtime
title: Spark runtime doctor — eventlog/plan/logs subcommands
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_spark_runtime.py -q
---

# Acceptance Criteria
- `forge-doctor spark eventlog DIR|FILE`: parses Spark event-log JSONL; detects executor lost, task skew, shuffle spill, GC pressure, single-task stages, task retries, long scheduler delay
- `forge-doctor spark plan FILE`: physical plan ops — CartesianProduct, BroadcastNestedLoopJoin, Exchange SinglePartition, global Sort, join strategy mix
- `forge-doctor spark logs FILE`: reuses error packs + runtime signatures (Lost executor, Stage retry, OOM)
- findings emitted as CheckResults (category runtime), text+json
- no Spark dependency; pure stdlib parsing
