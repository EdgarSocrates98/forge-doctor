# Contributing

Thanks for helping improve Forge Doctor Data. The bar: simple > useful > correct >
extensible. No speculative abstractions.

## Setup

```bash
git clone https://github.com/EdgarSocrates98/forge-doctor-data
cd forge-doctor-data
poetry install
poetry run pytest
poetry run ruff check .
poetry run mypy
```

Everything must be green before opening a PR.

## Adding a check

1. Pick a stable id from the category prefix (`REP`, `PY`, `DEP`, `GIT`,
   `SPARK`, `AWS`) — ids are public API and never change meaning.
2. Implement the `Check` protocol in the right module under
   `src/forge_doctor_data/checks/`:

   ```python
   @dataclass(frozen=True)
   class MyCheck:
       id = "REP009"
       title = "Something meaningful"
       category = "repository"

       def run(self, ctx: ProjectContext) -> list[CheckResult]:
           if not ctx.has_file("somefile"):
               return [self.result(Severity.INFO, "somefile not found", "Add it.")]
           return [self.result(Severity.PASS, "somefile found")]
   ```

3. Add the instance to the module's `CHECKS` list.
4. Add tests under `tests/unit/checks/` (use `tmp_path` fixtures — never scan
   the real repo in unit tests).
5. Document the rule in `docs/checks.md`: severity, description, why it
   matters, **when it is OK**, recommendation. Forge Doctor Data educates — say when
   a flagged pattern is acceptable.
6. Verify: `poetry run pytest && poetry run ruff check . && poetry run mypy`.

## Rules for checks

- Return `CheckResult`s; never print, never `sys.exit`.
- Never execute or import the analyzed project's code.
- Never read secret values into results (`~/.aws/credentials`, `.env`, …).
- Weak evidence → `INFO`, not `WARNING`/`ERROR`.
- Prefer `ast` over regex for Python code patterns.
- Keep it offline: no network calls.

## Beyond checks

- **Knowledge packs** — version facts (runtimes, compat changes, error
  signatures) live in `src/forge_doctor_data/knowledge/`; a pack ships schema
  version + `verified_at` + sources. `forge-doctor-data knowledge verify` must
  pass; the engine code does not change when a version goes EOL.
- **Lab scenarios** — add a dir under `labs/<domain>/` with a fixture
  project plus `expected.json` ground truth; `forge-doctor-data lab run` and
  `lab metrics` measure precision/recall against it.
- **Golden repos** — add a realistic mini-repo under `golden/repos/` and
  regenerate its snapshot with `forge-doctor-data golden update`; review the
  snapshot diff like code.
- **Policy packs** — org rules are declarative YAML/JSON in
  `.forge-doctor-data/policy/`; see the existing packs and
  `forge-doctor-data policy validate`.

## Tests

- `tests/unit/` — check internals, one behavior each.
- `tests/integration/` — CLI end-to-end via `typer.testing.CliRunner`.
- `tests/sample_projects/` — fake projects to scan.
- `tests/fixtures/` — shared fixtures (conftest).

## Releases

See [docs/roadmap.md](docs/roadmap.md). Publishing is manual and gated on a
maintainer decision; `release.yml` runs only via `workflow_dispatch`.
