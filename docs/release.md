# Release readiness — v1.0

Publishing is a deliberate maintainer decision: `release.yml` runs only
via `workflow_dispatch` on a `vX.Y.Z` tag and publishes to PyPI through
Trusted Publishing (OIDC, PEP 740 attestations). This page is the
pre-flight checklist — what must be true before `v1.0` is tagged.

## Hard gates (all must pass)

```bash
pytest                       # full suite green
ruff check .                 # lint clean
ruff format --check .        # format clean
mypy src                     # types clean
forge-doctor-data lab run         # scenario suites pass
forge-doctor-data lab metrics     # precision/recall within noise budget
forge-doctor-data golden run      # snapshot regression clean
forge-doctor-data bench run .     # no perf regression vs recorded baseline
forge-doctor-data knowledge verify  # all packs verified, none stale
```

CI mirrors this: `ci.yml` runs the quality gate on Python 3.11–3.13 and
the wheel-install smoke on ubuntu/windows/macos — the smoke step pins
the scan-report contract (`schema_version` == `3.0`,
`tool.name` == `forge-doctor-data`, full `summary` keys) and the
`schema contracts` command.

## Contract review (v1.0-specific)

Before tagging, a human confirms:

- `docs/api.md` stability rules are acceptable — `forge_doctor_data.api`
  `__all__` becomes semver-bound at 1.0.
- Both schema contracts are at their intended versions:
  `SCAN_SCHEMA_VERSION` (`scan -f json`, currently `3.0`) and
  `SCHEMA_VERSION` (artifact family, currently `1.0`).
- Every check id is documented (`tests/unit/test_docs.py` enforces).
- `CHANGELOG.md` `[Unreleased]` is moved under a `## [1.0.0]` heading.
- The active specs under `factory/specs/active/` are reviewed and
  archived (`loop-factory archive <id> --accepted`) — specs are
  archived only by explicit acceptance.
- Version fields bumped in lockstep: `pyproject.toml` `version` and the
  `__init__.py` fallback.

## Release steps

1. `git tag v1.0.0 && git push origin v1.0.0`
2. Actions → `release` → Run workflow.
3. Verify the GitHub Release artifacts and the PyPI upload
   (`dist/*` + PEP 740 attestations).
4. Post-release smoke: `pipx install forge-doctor-data && forge-doctor-data doctor .`
   on a clean machine.

## Deliberately out of scope for 1.0

- No `fix` subcommand (diagnostics proven, transforms not yet).
- No network/cloud calls in `scan`.
- No LLM dependency — all intelligence is deterministic.
