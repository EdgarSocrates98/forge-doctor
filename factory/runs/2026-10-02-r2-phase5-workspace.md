# Roadmap-2 Phase 5 run — Workspace Multi-Repository Intelligence

- Spec: `factory/specs/active/196-workspace-intelligence.md`
- Commit message: `feat(workspace): add multi-repo model and cross-repo links`

## Implemented

- `core/workspace.py`:
  - `discover_repos(root, ctx)` — marker-driven repo discovery
    (`pyproject.toml`, `*.tf`, `databricks.yml`, `airflow.cfg`, `.git`,
    `dags/` content); outermost marker dir wins; no markers → root repo.
  - `WorkspaceRepo` / `CrossRepoLink` / `WorkspaceModel` —
    `summary()`, `repo_of(entity_id)`, workspace `languages`.
  - `build_workspace_model(root, ctx)` — per-repo `DataPlatformGraph`s
    merged by canonical entity id; `repo:workspace:<name>` entities;
    derived repo-level `DEFINES` / `IMPLEMENTS` / `INVOKES` edges.
  - `EntityKind.REPO` + `RelKind.IMPLEMENTS` added to the canonical
    vocabulary.
- `cli/workspace.py` — `workspace inspect [--format json]`: repo table,
  cross-repo link table, merged-graph counts; JSON emits the full model.

## Link semantics (deterministic, evidence-gated)

- `DEFINES` — lifted from each repo graph's own IaC `DEFINES` edges.
- `IMPLEMENTS` — `.py` file with glue-code signals (awsglue / GlueContext
  / `Job(` / `getResolvedOptions`) whose normalized stem equals a
  defined glue job name. A filename alone never links.
- `INVOKES` — repo's graph has an `INVOKES` edge to a platform entity;
  internal `task:*` targets and same-repo invocations are suppressed.

## Folded-in fix

- `airflow_model`: bare `Operator(task_id=...)` expressions inside
  `with DAG(...)` bodies are now collected as tasks (previously only
  `var = Operator(...)` assigns) — restores task_count, wiring, and
  orchestrator `INVOKES` edges for the canonical context-manager form.

## Verified

- Fixture: `terraform-repo` (aws_glue_job orders-etl) + `glue-jobs`
  (orders-etl.py awsglue) + `airflow-dags` (GlueJobOperator) →
  `DEFINES` + `IMPLEMENTS` + `INVOKES` all converge on
  `compute_job:glue:orders-etl`.
- `pytest tests/unit/test_workspace.py tests/unit/adversarial/test_workspace.py`
  → 15 passed (spoofed filename, self-links, name normalization,
  duplicate DEFINES, empty workspace, determinism, marker collapse).
- `lab run` 10/10 · `golden run` 8/8 — no snapshot drift from the
  airflow fix.

## Open questions

- IMPLEMENTS currently covers Glue sources only; extending to
  EMR/Databricks/Lambda implementations is a natural follow-up — left
  explicit rather than guessing heuristics.
- Workspace-level contract/ownership across repos (one
  `platform-contract.yml` at root vs per-repo) is undecided.
