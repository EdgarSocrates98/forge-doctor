---
id: 128-airflow-runtime
title: Airflow stage 7 — runtime logs + cross-orchestrator graph links
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Final block of `prompt_evo_airflow.md`. Two pieces:
1. Runtime: `forge-doctor-data airflow logs <file>` over scheduler/worker/
   triggerer logs — deepened error pack (DAG import timeout, zombies,
   heartbeat, pool starvation, serialization failures).
2. Graph: link Airflow tasks → Glue/EMR/Databricks jobs (`GlueJobOperator`,
   `boto3.start_job_run`) → files → tables, and Control-M → Airflow DAG
   triggers — the multi-orchestrator chain from the reviewer's diagram.

# Acceptance Criteria
- `airflow logs` command (log file arg) over the error pack + airflow-
  specific extras; stays offline.
- Cross-domain: `GlueJobOperator(job_name=X)` ↔ IaC `aws_glue_job "X"` ↔
  script files — unambiguous-name links only; surfaced in `inspect` as a
  "Links" section; findings optionally annotated.
- Control-M `ctm run` + dag references considered only on exact match.

# Constraints
- No fuzzy matching — exact name equality only; no execution.
