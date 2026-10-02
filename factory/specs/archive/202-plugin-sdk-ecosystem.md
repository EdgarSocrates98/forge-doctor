---
id: 202
title: Plugin SDK & Ecosystem
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_sdk.py tests/unit/test_plugin_ext.py -x -q
  - python -m forge_doctor plugins doctor
---

# Roadmap-3 Phase 1 - Plugin SDK & Ecosystem

## Context

Third parties must be able to build analyzers/rules/adapters/packs
without importing internals. `plugins/protocol.py` already has the
`Check` protocol, `PluginDescriptor` v2 (api_version +
requires_forge_doctor gating), and pre-load trust gates — but the SDK
is not a stable public surface, there is no scaffolding, no integrity
pinning, no strict-deny mode, and no install flow.

## Acceptance Criteria

- `src/forge_doctor/sdk.py` — the stable plugin-author surface:
  re-export `Check`, `CheckBase`, `CheckResult`, `Severity`,
  `Confidence`, `EvidenceKind`, `PluginDescriptor`, `PluginIdentity`,
  `ProjectContext`, `CURRENT_API_VERSION`, `SUPPORTED_API_VERSIONS`,
  `ENTRY_POINT_GROUP`. `__all__` pinned by test.
- `forge-doctor plugins init <name>` — scaffold a plugin package:
  pyproject with the `forge_doctor.checks` entry point, a `Check`
  subclass importing only `forge_doctor.sdk`, a test stub. Deterministic
  output; refuses to overwrite existing files.
- `forge-doctor plugins lock` / `plugins verify` — integrity pinning:
  lock records each installed plugin distribution's name/version and a
  content digest (dist-info RECORD) into `.forge-doctor/plugins.lock`;
  verify re-digests and reports ok/changed/missing. Offline, no signing
  infra.
- Strict trust mode — `[tool.forge-doctor.plugins] mode = "strict"`
  denies every plugin not in `trusted` (default-deny); current
  default-permit behavior unchanged when unset. `plugins list` shows
  `untrusted` status for denied plugins.
- `forge-doctor plugins install <dist>` — thin wrapper: resolves to
  `pipx inject forge-doctor <dist>` (pipx env) or `pip install`,
  `--dry-run` prints the command only, post-install runs the load/compat
  validation and reports. Installer is injectable for tests.
- `docs/plugins.md` documents the SDK surface, template, trust levels
  (strict/default/allow), lock/verify, and install.
- Tests: sdk exports pinned, scaffold renders + loads, lock/verify
  roundtrip + tamper detection, strict mode denies, install dry-run.

## Constraints

- No network calls in tests; `install` shells out only at user request.
- Trust gates must never require loading plugin code.
- Registry/marketplace (`plugin search`) is **not** in scope.

## Open questions

- Public registry/index format for `plugin search` — deferred; likely a
  signed JSON index, needs product decision on hosting.
