# Run — Airflow Intelligence, stage 1 (spec 122)

**Prompt:** `prompt_evo_airflow.md` — a seven-block Airflow cycle. Stage 1 =
semantic model + detection + structural checks + `airflow inspect`.

## Specs written

| spec | scope | state |
|---|---|---|
| 122-airflow-model | AirflowProjectModel, AIR000-130, `airflow inspect`, packs | **active — built** |
| 123-airflow-sched-graph | trigger rules, cycles, cross-DAG, `airflow schedule|graph` | inbox |
| 124-airflow-performance | parse pressure, pools, `airflow capacity|parse` | inbox |
| 125-airflow-modern | assets graph, event-driven, dynamic mapping, deferrable | inbox |
| 126-airflow-reliability | retries/timeout combos, idempotency, XCom | inbox |
| 127-airflow-migrate | 2→3 advisor + providers pack, `airflow migrate|providers` | inbox |
| 128-airflow-runtime | `airflow logs`, Control-M→Airflow→Glue graph links | inbox |

## Shipped (122)

- `analyzers/airflow_model.py` — `AirflowModel` + `AirflowDag`/`AirflowTask`/
  `AirflowEdge`. Index-flagged `airflow*` files get ONE targeted
  `ast.parse` (CallSite lacks non-string kwargs and `>>` edges):
  - DAGs: `with DAG(...) as d`, `d = DAG(...)`, `@dag` defs; dag_id from
    kwarg OR positional arg OR var fallback; schedule/catchup/
    max_active_runs/start_date_dynamic (any runtime date call).
  - Tasks: `*Operator`/`*Sensor` assigns, `@task` TaskFlow defs (nested in
    `@dag` bodies too); bound via with-block or `dag=` kwarg; wired via
    `>>`/`<<` chains (nested-BinOp decomposition — both ops return the
    right operand), `set_upstream/downstream`, `chain`, `cross_downstream`,
    or TaskFlow call.
  - Parse-time calls: module-scope calls to pack `parse_time_modules`
    (requests/boto3/urllib/...) + `Variable.get` — deduped per (root,line)
    (`requests.get().json()` counts once).
  - Providers: `airflow.providers.<seg>` from index imports.
- `checks/airflow.py` — AIR000 anchor, AIR002 dup dag_id, AIR003 empty DAG,
  AIR004 orphan task, AIR013 dynamic start_date, AIR021/AIR025 parse-time
  calls, AIR040 poke sensor, AIR042 sensor no-timeout, AIR100 retries
  w/o delay, AIR130 undeclared provider (PEP-508-normalized dep names).
- `cli/airflow.py` — `airflow inspect` (DAGs/tasks-by-operator/sensor
  deferrable split/edges/providers/parse-time calls/severity-sorted risks).
- `knowledge/airflow/objects.json` (sensors, parse-time modules, provider
  package map) + `knowledge/errors/airflow.json` (6 families → `diagnose`).
- Registration: `checks/__init__`, `cli/__init__`, ERROR_DOMAINS. Docs:
  SPEC T35 + §I + AIR### ids, README row + command, checks.md, CHANGELOG.

## E2E evidence

Fixture DAG with `datetime.now()` start_date, parse-time `requests.get` +
`Variable.get`, a poke sensor, `retries=5` no-delay, undeclared amazon
provider, orphan task → all flagged with correct file:line. `diagnose` maps
"Detected zombie" → AIR-E002, pool log → AIR-E003.

## Latent quirks found

- `a >> b >> c` is a nested BinOp — both `>>`/`<<` return the right operand,
  so chain tails come from `left.right`; visitor dedupes by (src,dst).
- `requests.get().json()` double-flags the module — deduped per line.
- `Defaults`-style ordering bug pattern repeated: `@dag` bodies need their
  own nested `@task` collection pass.

## Verification

528 passed · ruff check clean · ruff format clean · mypy 77 files clean.

**Parked at review gate.** Spec stays in `active/` until accepted.
Next ready: 117 (Control-M graph) or 123 (Airflow sched/graph).
