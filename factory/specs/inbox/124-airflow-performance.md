---
id: 124-airflow-performance
title: Airflow stage 3 — performance — parse pressure, pools, capacity, `airflow capacity|parse`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 3 of `prompt_evo_airflow.md`. Scheduler parse pressure (top-level
work is re-executed every parse pass) and resource/capacity posture.

# Acceptance Criteria
- Extend parse-time analysis: filesystem/config loads at parse time
  (AIR023/AIR026), dataframe ops at parse time (AIR024), dynamic DAG
  factory detection (loops producing DAGs → count in `airflow parse`).
- Pools/concurrency: no pool on expensive workload (AIR090),
  max_active_tasks/max_active_runs too high (AIR093/AIR094),
  high fan-out without resource control (AIR096).
- `forge-doctor airflow parse <path>` — static parse-pressure ranking
  (top-level statements, imports, external calls, dags/file, tasks/file)
  — explicitly NOT a benchmark.
- `forge-doctor airflow capacity <path>` — per-DAG task/fanout/pool/
  max_active_runs summary + risks.

# Constraints
- Heuristics conservative; the report stays "static parse-pressure".
