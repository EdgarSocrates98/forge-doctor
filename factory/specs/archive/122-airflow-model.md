---
id: 122-airflow-model
title: Airflow Intelligence stage 1 — AirflowProjectModel, AIR000-130 checks, `airflow inspect`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_airflow_model.py tests/unit/checks/test_airflow.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_airflow.md` opens a seven-block Airflow cycle. Stage 1 = semantic
model + detection + first structural checks + `airflow inspect`. Blocks 2-7
are specs 123-128.

Airflow DAGs are Python — the model consumes `project_index(ctx)` to flag
airflow files (imports of `airflow*`), then runs ONE targeted `ast.parse`
per flagged file (like `sql_ast` does for `.sql`) because `CallSite` keeps
only string-literal kwargs and edges (`>>`, `chain`, `set_downstream`) are
not calls. Checks never parse (V11).

# Acceptance Criteria

## Model (`analyzers/airflow_model.py`) — stdlib only
- `AirflowDag` record: dag_id (kwarg or var fallback), file, line, schedule
  raw, catchup (bool|None), start_date_dynamic (True when start_date is a
  call like `datetime.now()`/`utcnow()`/`timedelta`...), task_count,
  max_active_runs.
- `AirflowTask` record: task_id (kwarg or var name), operator class,
  file, line, dag-bound (inside `with DAG` block or `dag=` kwarg),
  is_sensor (class endswith `Sensor` or in pack list), deferrable kwarg,
  timeout/execution_timeout present, retries value, retry_delay present,
  wired (name in any edge/chain/set_* call).
- `AirflowModel` memoized on ctx: `files`, `dags`, `tasks`, `edges`
  (name→name pairs), `providers` (airflow.providers.<x> top segment),
  `has_airflow`.
- Recognize `with DAG(...) as d`, `d = DAG(...)`, `@dag` decorated defs,
  `XxxOperator(...)`, `XxxSensor(...)`, `@task*` TaskFlow funcs; edges from
  `a >> b` / `a >> [b,c]` / `a << b` / `set_upstream|set_downstream` /
  `chain(...)` / `cross_downstream(...)`.
- Top-level external calls flagged per file: module-scope call sites whose
  dotted root is in pack `parse_time_modules` (requests, urllib, boto3,
  psycopg2, sqlalchemy, httpx) or `Variable.get` — file+line recorded.
- Only files flagged via index imports (`airflow`, `airflow.*`) are parsed;
  deterministic ordering throughout.

## Checks (`checks/airflow.py`, category `airflow`, unconditional)
- `AIR000` anchor (PASS none / INFO counts).
- `AIR002` duplicate dag_id across files — WARNING.
- `AIR003` DAG with no tasks — WARNING.
- `AIR004` orphan task — unwired task in a DAG that has >1 task — INFO.
- `AIR013` dynamic `start_date` (call expr, e.g. `datetime.now()`) — WARNING.
- `AIR021` external call at parse time (network/client modules) — WARNING.
- `AIR025` `Variable.get()` at parse time — WARNING.
- `AIR040` sensor in poke mode (`deferrable` not True) — INFO.
- `AIR042` sensor without timeout/execution_timeout — INFO.
- `AIR100` retries>0 without retry_delay — INFO.
- `AIR130` imported provider (`airflow.providers.<x>`) not declared as
  `apache-airflow-providers-<x>` in pyproject deps — WARNING; skipped when
  no pyproject.

## Command (`cli/airflow.py`, `airflow` typer group)
- `forge-doctor-data airflow inspect [path]` — DAGs (id/schedule/tasks), tasks by
  operator, sensors deferrable count, edges, providers, risks (same shape as
  `controlm inspect`). Bare `airflow` → hint + exit 2; empty repo clean.

## Knowledge packs
- `knowledge/airflow/objects.json` (schema_version 2 + sources):
  sensor type names, parse-time module roots, provider prefix map,
  known operators→provider hint.
- `knowledge/errors/airflow.json` — seed families (DAG import timeout,
  zombie/task adoption, pool starvation, scheduler heartbeat) into
  `diagnose`'s ERROR_DOMAINS.

## Docs/tests
- checks.md `## Airflow` section; README row + command; CHANGELOG; SPEC T35
  + `airflow` in the interface list + `AIR###` in check ids.
- Tests: model facts; each check pos+neg; TaskFlow/`@dag` forms; edges via
  `>>`, `set_downstream`, `chain`; deterministic order; CliRunner for
  `airflow inspect`; non-airflow repo silent; warm-cache path (facts come
  from index, tree re-parse only on flagged files).

# Constraints
- No `apache-airflow` dependency — pure AST.
- Provider map: `airflow.providers.amazon` → `apache-airflow-providers-amazon`
  (segment, not full submodule path).

# Review Notes
- Deferred to 123-128: trigger-rule semantics, asset graph + event-driven,
  pools/capacity + `airflow capacity|parse`, retries/idempotency/XCom depth,
  migrate 2→3 + providers depth, runtime logs + Spark/Glue/Control-M links.
