---
id: 102-plugin-trust-boundary
title: Plugin trust gate BEFORE ep.load()
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/test_plugins.py tests/unit/test_cli_v2.py -q -k plugin
  - python -m pytest -x -q
---

# Context
`load_plugins()` resolves entry points via `ep.load()` — executing arbitrary
plugin code — BEFORE `_plugin_allowed` checks the allowlist. A
non-allowlisted plugin still executes code on import. Check-ids can't be a
load barrier (only known post-load).

# Acceptance Criteria
- New config: `[tool.forge-doctor-data.plugins] trusted = [dist|entry-point names]`
  gates at entry-point METADATA level — disallowed plugins are never loaded.
  No `ep.load()` on untrusted plugins.
- Post-load check filtering: `[tool.forge-doctor-data.plugins.checks] enabled =
  [check ids]` (or `disabled`), applied after load for trusted plugins.
- Backward compat: existing `allow = [...]` keeps working — dist/entry-point
  entries act as trust gate, check-id entries as post-load filter. Documented.
- `--no-plugins` unchanged: skips all loading.
- plugins doctor/validate report trust decisions (loaded/skipped-untrusted).
- Tests: untrusted plugin never loaded (assert loader not invoked), trusted
  plugin loads, check-level filter drops disallowed check ids.

# Constraints
- Entry-point metadata (name, dist name/version) is available without load —
  use `importlib.metadata` EntryPoint attributes only in the gate.
