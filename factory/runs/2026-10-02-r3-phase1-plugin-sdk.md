# Run — R3 Phase 1: Plugin SDK & Ecosystem

Spec: `factory/specs/active/202-plugin-sdk-ecosystem.md`
Date: 2026-10-02 · Agent: claude · Result: built, awaiting review

Also authored Roadmap-3 spec set 202–211 mapping the strategy doc's ten
post-v1.0 items (plugin SDK, governance, fleet, historical, change,
incremental, safe-fix, experiments, knowledge supply chain, ecosystem
contracts).

## Delivered

- `src/forge_doctor/sdk.py` — stable author surface: `Check`,
  `CheckBase`, `CheckResult`, `Severity`, `Confidence`, `EvidenceKind`,
  `PluginDescriptor`, `PluginIdentity`, `ProjectContext`,
  `CURRENT_API_VERSION`, `SUPPORTED_API_VERSIONS`, `ENTRY_POINT_GROUP`;
  `__all__` pinned by test. Consumer SDK stays `forge_doctor.api`.
- `src/forge_doctor/plugins/manager.py` —
  - `scaffold_plugin(name, dest)`: complete plugin package (pyproject
    entry point + sdk-only check + descriptor + test stub); refuses
    overwrite, validates name.
  - `lock_plugins(root)`: `.forge-doctor/plugins.lock` pins every
    installed plugin dist (name, version, sha256 over sorted per-file
    digests recomputed from content — RECORD tampering caught).
  - `verify_plugins(root)`: `ok | changed | missing | added`.
  - `install_plan`/`install_plugin(dist, runner=...)`: `pipx inject
    forge-doctor <dist>` when pipx exists else `pip install`; runner
    injectable — no network in tests.
- Strict trust mode — `[tool.forge-doctor.plugins] mode = "strict"`:
  default-deny, only `trusted` passes the pre-load gate; `allow`
  identities no longer suffice. Status line names strict explicitly.
- CLI: `plugins init|lock|verify|install` (+ `--dry-run`); existing
  `list`/`validate`/`doctor` honor strict mode.
- Docs: `docs/plugins.md` gained SDK surface, scaffolding, strict mode,
  integrity pinning, install sections; `docs/api.md` distinguishes
  consumer (`api`) vs author (`sdk`) surfaces; README lists new cmds.
- Tests: `test_sdk.py` (surface pinned, versions match, sdk-only check
  runs) + `test_plugin_ext.py` (scaffold validity + runnable check,
  lock/verify roundtrip + missing/added/tamper detection, strict deny +
  trusted-pass, config parse, install plan/runner injection) — 14 new.

## Verification

- Focused: 14 pass; `plugins`/`api`/`docs` suites still green (44).
- mypy clean (157 files), ruff + format clean.
- End-to-end: scaffolded `forge-doctor-snowflake` → descriptor
  `check_compatibility` ok, check runs, `lock`/`verify`/`--dry-run` work.

## Fixes during implementation

- `reason` local in `load_plugins` collided with loop variable reuse
  (mypy unreachable-code errors) → renamed `untrusted_reason`.
- `dist.locate_file()` returns `SimplePath` → `Path(str(...))`.

## Open questions

- Registry/marketplace (`plugin search`) deferred — spec 202 records it.

Stays in `active/` — archive only on explicit human acceptance.
