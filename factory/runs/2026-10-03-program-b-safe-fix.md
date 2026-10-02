# Run: Program B — Safe Fix Intelligence (spec 208)

- **Initial HEAD**: `5806bd0`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/208-safe-fix.md`

## Scope

Controlled autofix with a strict safety taxonomy. Diagnostics stay the
product; `fix` only proposes bounded text transforms for findings whose
repair is a pure, reversible edit.

## Files changed

- `src/forge_doctor/core/fixes.py` — **new** (~360 lines): `FixAction`
  model (`check_id`, `check_ids`, `fingerprints`, `file`, `title`,
  `fix_class`, `transform`, `before`/`after`, `create`/`delete`,
  `guidance`, `superseded`), transform registry keyed by check id,
  `MANUAL_ONLY_CATEGORIES` hard-coded exclusion (terraform, iac,
  lakeformation, aws, dynamodb, neptune, serverless, architecture,
  streaming(-bus), controlm, glue, stepfunctions), `plan_fixes` with
  deterministic same-file superseding, `apply_fix` with root-escape +
  stale-source + manual-class refusal, unified-diff rendering.
- `src/forge_doctor/cli/fix.py` — **new** `forge-doctor fix <path>`:
  dry-run diffs by default; `--apply` writes `safe` only;
  `--apply --class review` adds review tier; `--json` audit output.
- `src/forge_doctor/cli/__init__.py` — register module + help panel.
- `tests/unit/test_fixes.py` — **new**, 24 tests.
- `README.md`, `docs/checks.md`, `docs/getting-started.md`,
  `CHANGELOG.md` — user-facing docs.

## Transforms shipped

| transform | checks | class | edit |
|---|---|---|---|
| `requires_python_default` | PY002 | safe | insert `requires-python = ">=3.10"` under `[project]` |
| `gitignore_patterns` | GIT002, GIT003 | safe | append missing ignore patterns (basename for sensitive files, `__pycache__/`, `*.py[cod]`, `*.egg-info/` for artifacts) |
| `create_gitignore` | REP004 | safe | create `.gitignore` with standard Python patterns |
| `drop_requirements_txt` | REP007 | review | delete `requirements.txt` when it conflicts with `poetry.lock` |

GIT002's "untrack the file" half stays manual (`git rm --cached` is a
git op, explicitly out of scope); the transform only covers the
`.gitignore` bookkeeping.

## Guardrails

- `register_transform` raises `FixRefused` for MANUAL_ONLY categories —
  a transform cannot even be registered for e.g. TF001.
- `apply_fix` raises on `manual` class regardless of flags; returns
  `stale-source`/`superseded`/`refused` statuses instead of writing.
- Target paths must resolve inside the scanned root.
- Results covered by an action get `fixable=True` set on the
  `CheckResult` (field already existed in the model/JSON contract).

## Tests / gates

- `pytest tests/unit/test_fixes.py`: **24 passed**
- `pytest tests/unit/ -k fix -x -q`: 32 passed (spec verification)
- `ruff check` / `ruff format` / `mypy` on touched files: clean

## Known limitations

- Transforms are whole-file text edits, not AST-aware patches; the
  stale-source byte check is the guard against drift.
- `requires-python` default (`>=3.10`) is the tool's own support floor;
  teams wanting a different floor still choose it manually (the fix
  only fills a missing declaration).
- Patch-file export deferred per spec open question.

## Open questions

None added. Spec remains in `active/` pending human review.
