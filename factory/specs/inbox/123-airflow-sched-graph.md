---
id: 123-airflow-sched-graph
title: Airflow stage 2 — scheduling + dependency graph, `airflow schedule|graph`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 2 of `prompt_evo_airflow.md`, on 122's `AirflowModel`. Trigger rules,
cycles, cross-DAG edges, schedule semantics.

# Acceptance Criteria
- Deepen edges into a per-DAG task graph: unreachable task (AIR005),
  circular dependency (AIR006), trigger-rule hazards — `all_success` join
  after branch (AIR111), cleanup task that may never run (AIR112).
- Cross-DAG: `TriggerDagRunOperator`/`ExternalTaskSensor` targets resolved
  against known dag_ids — missing target (AIR124), cyclic cross-DAG (AIR123).
- Scheduling: missing schedule (AIR010), catchup=True with dynamic/expensive
  history (AIR012), cron parse sanity (AIR015 too-frequent heuristic),
  max_active_runs high (AIR016).
- `forge-doctor airflow schedule <path>` — effective schedule per DAG +
  risks; `forge-doctor airflow graph <path>` — text/dot of the DAG graphs.

# Constraints
- Small deterministic graph impl (no new deps).
