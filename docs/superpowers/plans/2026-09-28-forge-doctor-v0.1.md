# Forge Doctor v0.1 Implementation Plan

**Goal:** pipx-installable Python CLI that deterministically diagnoses data-engineering projects across 6 categories with ~24 structured checks, Rich console + stable JSON output, plugin discovery via entry points, full test/lint/type/CI/packaging pipeline.

**Architecture:** Layered per docs/architecture.md — Typer CLI → CheckRunner over a CheckRegistry → Check implementations per category → analyzers (AST, pyproject) → memoized ProjectContext → renderers (Rich/JSON).

**Tech stack:** Python >=3.11, Poetry 2.x (PEP 621 + poetry-core>=2.0), Typer, Rich, packaging, pytest+pytest-cov, ruff, mypy, GitHub Actions.

**Spec:** SPEC.md (§I interfaces, §V invariants are binding)

## Global constraints

- `requires-python = ">=3.11"`; deps pinned `typer>=0.15`, `rich>=13`, `packaging>=24`
- No runtime deps beyond those three. stdlib: ast, tomllib, importlib.metadata, subprocess, shutil, configparser, fnmatch, dataclasses, enum, pathlib
- Check ids: REP001-008, PY001-007, DEP001-006, GIT001-003, SPARK001-006, AWS001-004
- Exit codes: 0 clean / 1 errors / 2 internal
- Never execute/import analyzed code; never print secret values; offline only
- Default dir excludes: .git .venv venv node_modules dist build __pycache__ .pytest_cache .mypy_cache .ruff_cache .idea .hg .tox

## Tasks

- [ ] **T1 Scaffold** — pyproject.toml (PEP 621 + [project.scripts] forge-doctor, [dependency-groups] dev, ruff/mypy/pytest config), .gitignore (extend: keep `prompt*.md`, add python defaults), .editorconfig, package skeleton with __version__, `poetry install` green, `poetry run forge-doctor --help` works.
- [ ] **T2 Core** — core/models.py (Severity, CheckResult, ScanReport), core/context.py, core/registry.py, core/runner.py, core/config.py, core/traversal.py, plugins/protocol.py, core/errors.py + unit tests (registry, runner failure isolation, traversal excludes, config load, V8 empty-dir).
- [ ] **T3 Output + CLI** — output/summary.py, output/json_renderer.py, output/console.py, cli.py (scan + category cmds + plugins + version + checks), exit codes, --quiet/--ignore/--fail-on/--format/--verbose; CliRunner tests.
- [ ] **T4 Checks A** — checks/repository.py (REP001-008), checks/python_env.py (PY001-007), checks/git_checks.py (GIT001-003), checks/__init__.py builtin registry, tests/sample_projects/{clean,problematic,poetry_project,empty}_project, unit+integration tests.
- [ ] **T5 Checks B** — checks/dependencies.py (DEP001-006 incl. poetry lock/packaging), analyzers/spark_ast.py + checks/spark.py (SPARK001-006), checks/aws.py (AWS001-004 sanitized), tests incl. AST line-number assertions and AWS secret-safety tests.
- [ ] **T6 Plugins** — plugins/discovery.py (entry_points group forge_doctor.checks, defensive load), `plugins` command output, test with monkeypatched entry points proving discovery→registry→runner chain.
- [ ] **T7 Docs** — README.md (all §35 sections), CONTRIBUTING.md (how to add a check), CHANGELOG.md, LICENSE (MIT), docs/checks.md (every check: severity/description/why/when-ok/recommendation), docs/roadmap.md.
- [ ] **T8 CI** — .github/workflows/ci.yml (matrix py 3.11-3.13: install, ruff check+format --check, mypy, pytest --cov, poetry build) + release.yml (workflow_dispatch only: build, tag→GitHub Release→PyPI documented, no real secrets).
- [ ] **T9 Verify** — ruff check ., ruff format --check ., mypy, pytest, poetry build, pipx install dist/*.whl, real `forge-doctor --version/--help/scan ./scan --format json/plugins/checks`, sample projects, pipx uninstall. Record evidence.

## Rulings (pre-flight)

- Poetry checks live under category `dependencies` (DEP ids) — spec §9 lists 6 categories, §14 Poetry checks are packaging/deps.
- `python -m forge_doctor` supported via __main__.py → cli.app().
- Category commands reuse `run_scan()` helper — no duplicated engine logic (§11).
- Work happens directly on `main` — greenfield repo, initial implementation is the deliverable, no branch to protect.
