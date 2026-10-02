# Run: Program A — Continuous Incremental Semantic Analysis (spec 207)

- **Initial HEAD**: `eb9f3b09bfde6dd4abdbee9ca696919a15b913d7`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/` spec 207 (Continuous Incremental)

## Scope

Program A of the consolidation roadmap: make scanning semantically
incremental. Per the spec's pragmatic cut, the shipped slice is
**check-level incrementality**: declared evidence domains → file change
classification → rerun only affected/unbounded checks → merge with
prior results preserving the full-scan output contract. Semantic-fact
sub-file granularity (A.2–A.4 in the roadmap narrative) remains future
work — the dependency index here is check→domain, not fact→entity.

## Files changed

- `src/forge_doctor/core/incremental.py` — **new** (~400 lines):
  `EvidenceDomain` vocabulary (`FILES`, `PROJECT`, `ENV`, `GIT`, `HOST`,
  `PYTHON`, `RUNTIME`, `SQL`, `GRAPH`, `UNBOUNDED`, + one per analyzers
  module), `MODULE_DOMAINS` declared map covering every
  `forge_doctor.checks.*` module, `classify_path`, `file_states`/
  `detect_changes` (mtime+size), `IncrementalPlan` (rerun/reuse),
  `ResultStore` (versioned per-check results in the user cache dir),
  `result_to_stored`/`result_from_dict` symmetric round-trip.
- `src/forge_doctor/plugins/protocol.py` — `CheckBase.evidence_domains`
  optional class attr (plugins can declare domains; `None` → module
  map → conservative `UNBOUNDED`).
- `src/forge_doctor/core/runner.py` — `CheckRunner.run_incremental()`:
  plans over selected checks, executes rerun set through the normal
  path, merges cached prior results (fingerprint-stable), records
  `runner.last_plan`/`runner.reused` for stats.
- `src/forge_doctor/core/service.py` — `ScanRequest.incremental`;
  service loads `ResultStore` when incremental or cache enabled,
  routes to `run_incremental`, saves fresh states+results post-scan.
- `src/forge_doctor/cli/common.py` — `IncrementalOpt`, threading
  through `_ScanCli`/`_run_scan`, watch-loop incremental stats line
  (`incremental: N file(s) changed, X checks rerun, Y reused`).
- `src/forge_doctor/cli/scan.py` — `--incremental` on `scan`.
- `tests/unit/test_incremental.py` — **new**, 31 tests: domain
  coverage over all built-in check modules, path classification,
  change detection, plan rerun/reuse partitioning, store round-trip +
  malformed rejection, **incremental≡full invariant** after tf/py/
  cosmetic/deleted edits, cold-start, service integration.
- `CHANGELOG.md` — Added entry under Unreleased.

## Design decisions

- **Conservative correctness**: anything without a declared bounded
  domain is `UNBOUNDED` → always reruns. Platform rules (PLAT*),
  contracts, migrations, what-if, graph-analytics checks are all
  unbounded — they see the whole `DataPlatformGraph`.
- **Full scan is the reference**: reuse only when the check's domains
  are disjoint from changed-file domains; result sets are asserted
  identical to a full scan in tests.
- **Host reads stay behind `ProjectContext`** (hermetic boundary
  preserved): incremental adds no new ambient reads.
- Store lives beside the existing AST/facts cache dir
  (`FORGE_DOCTOR_CACHE_DIR` override honored); schema-versioned.

## Tests / gates

- `pytest tests/unit/test_incremental.py`: **31 passed**
- `pytest tests/unit/ -k incremental -x -q`: 31 passed (spec verification)
- `pytest tests/unit/ -x -q`: **1334 passed**
- `ruff check` / `ruff format` / `mypy` on touched files: clean

## Dogfood

`forge-doctor scan --incremental --cache --stats` on this repo:
cold run populates store; warm run reports
`incremental: 0 file(s) changed, 31 checks rerun, 97 reused` —
97 checks served from prior results, only always-run/unbounded rerun.

## Known limitations

- Invalidation granularity is check→evidence-domain, not
  fact→entity→graph-fragment (A.2–A.4 deferred). A single `.tf` edit
  still reruns all terraform-domain checks.
- Change detection is mtime+size (fast, not content-hash); cosmetic
  edits that touch mtime rerun the file's domains — safe, just less
  reuse.
- Result store is per-root; `--diff`/baseline paths unchanged.

## Open questions

None added. Spec remains in `active/` pending human review/acceptance.
