---
id: 196
title: Workspace Multi-Repository Intelligence
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_workspace.py tests/unit/adversarial/test_workspace.py -x -q
  - python -m forge_doctor workspace inspect --path <workspace>
  - python -m forge_doctor lab run
  - python -m forge_doctor golden run
---

# Roadmap-2 Phase 5 - Workspace Multi-Repository Intelligence

## Context

Real platforms span repos: terraform declares the glue job in one repo,
python implements it in another, airflow invokes it in a third. Per-repo
scans miss the topology. This phase merges per-repo platform graphs into
one WorkspaceModel with repo entities and cross-repo links.

## Acceptance Criteria

- `discover_repos(root, ctx)` — a directory is a repo when it directly
  contains a marker (`pyproject.toml`, `*.tf`, `databricks.yml`,
  `airflow.cfg`, `.git`, `dags/` content); outermost marker dir wins;
  no markers → the root is a single repo.
- `build_workspace_model(root, ctx)` → `WorkspaceModel{repositories,
  graph, links}`: per-repo `DataPlatformGraph`s merged (canonical entity
  ids converge across repos) plus `repo:workspace:<name>` entities.
- Cross-repo edges: `repo DEFINES E` (IaC declares it), `repo IMPLEMENTS
  E` (glue code file whose normalized stem matches the job name),
  `repo INVOKES E` (repo's workflows target it). Internal `task:*`
  targets and same-repo invocations are not links.
- `forge-doctor workspace inspect [--format json]` prints repos,
  languages, markers, the merged-graph stats, and the link table.
- Tests: discovery rules, convergence, all three link kinds, spoofed
  filenames (no glue code → no IMPLEMENTS), self-links suppressed,
  duplicate DEFINES (first repo wins), empty workspace, determinism.

## Constraints

- Links are derived evidence (`EvidenceKind.DERIVED`), never fabricated:
  IMPLEMENTS requires real glue-code signals (awsglue/pyspark imports),
  INVOKES requires an actual graph edge from the caller repo.
- Reuses existing per-repo scanning; no new parsers.

## Review Notes

- Folding in: `airflow_model` now collects bare `Operator(...)`
  expressions inside `with DAG(...)` bodies — the canonical
  context-manager form was previously invisible to task extraction.
