---
id: 126-airflow-reliability
title: Airflow stage 5 — reliability — retries, timeouts, idempotency, XCom
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 5 of `prompt_evo_airflow.md`. Idempotency and retry storms — combined
signals, not single values.

# Acceptance Criteria
- Idempotency: `datetime.now()`/`date.today()` inside task bodies used for
  output paths (AIR032), unguarded write patterns (AIR030 — writeTo
  `.append()`/unguarded INSERT in retried tasks, cross with iceberg model
  when both present).
- Retry posture: retries without delay (AIR100 exists) + excessive count
  (AIR101), retry storm (AIR102 — high retries × short delay × many tasks),
  missing exponential_backoff on remote-dependency tasks (AIR103),
  no execution_timeout (AIR104).
- XCom: explicit `xcom_push`/`return` of dataframe-ish objects (AIR061),
  manual xcom_pull + TaskFlow redundancy (AIR063), ambiguous multi-upstream
  xcom_pull (AIR064).
- Severity combos: single-signal INFO → combined WARNING.

# Constraints
- Task-body analysis stays intra-file; no call-graph explosion.
