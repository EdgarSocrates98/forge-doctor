# R3 Phase 3 - Fleet / Estate Intelligence (spec 204)

## Built
- `core/fleet.py` — `load_manifest` (yaml/json file, `repos:` str or
  `{path,name}` entries, `root:` workspace-discovery, or a bare
  directory) + `build_fleet_model` producing a `WorkspaceModel`.
- `core/workspace.py` — merge logic extracted into `merge_repos(root,
  repos)`, shared verbatim by workspace discovery and fleet (no
  duplicated graph-merge logic, per spec constraint).
- `cli/fleet.py` — `fleet inspect <manifest|dir>` census,
  `fleet query <m> runtimes|capability <id>|dependents <glob>|findings
  <check>`, `fleet report` (entities by kind/domain, findings by
  severity/category/repo) — text + JSON.
- Capability query resolves entity version from any versioned attr
  (glue_version, dbr, runtime, …) — `attr("version")` alone misses
  domain-specific keys, silently producing UNKNOWN.
- `docs/fleet.md` + README command entries.

## Verified
- 3-repo manifest estate: merged graph (7 entities, 3 cross-repo links:
  DEFINES/IMPLEMENTS/INVOKES across terraform/glue-jobs/airflow-dags).
- All four queries + report exercised; capability bucketing works
  (glue entity → CONDITIONAL for ICEBERG_MERGE_WRITE, honest).
- 12 unit tests green.

## Scale honesty
- Fixture scale (~3-10 repos) tested; per-repo scan in
  `query findings`/`report` is linear; no remote fetching (deferred
  per spec).
