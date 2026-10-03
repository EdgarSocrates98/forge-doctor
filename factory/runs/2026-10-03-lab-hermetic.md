# Run: lab/golden hermetic scan boundary

**Date:** 2026-10-03
**Scope:** CI-only `test_metrics_perfect_run` precision regression (1.0 expected, 0.667 on CI).

## Root cause

Forge Lab ran `builtin_checks()` against a plain `ProjectContext`, so
host state leaked into "deterministic" results:

- `AWS002` read `~/.aws/config` (PASS locally, WARNING on CI) —
  the observed CI failure.
- `ctx.git` ascended to ancestor repos — CI's `.pytest_tmp` basetemp
  inside the checkout made GIT checks evaluate *forge-doctor-data's own*
  tracked files.
- `shutil.which` (`aws`, `poetry`) and `ctx.env`/`python_version` varied
  per machine.

## Fix

- `ScanOptions.hermetic: bool` — one opt-in flag on the context.
- `ProjectContext`: `env` → `{}`, `python_version` → `None`,
  `which(name)` → `None`, `home` → project root, `git` scoped to
  `root/.git` only (a scenario can still `git init` for git checks).
- `checks/aws.py` + `checks/dependencies.py` route all host reads through
  `ctx.env` / `ctx.home` / `ctx.which` (no direct `os.environ` /
  `Path.home` / `shutil.which` in checks).
- `lab.run_scenario` + `golden.run_golden`/`update_golden` construct
  hermetic contexts; normal scans unchanged.
- `AWS002` added to `labs/_defaults.json` allowed findings (a fixture
  genuinely has no AWS config — the warning is deterministic now).
- Golden `findings.json` snapshots regenerated (now machine-independent).

## Evidence

- `test_scenario_isolated_from_host_state` — AWS_REGION env var,
  `AWS_ACCESS_KEY_ID`, fake `~/.aws/config`, and an ancestor `git init`
  all present: AWS002 still warns, GIT001 still reports not-a-repo.
- `pytest -q`: 1368 passed. ruff/format/mypy clean.
