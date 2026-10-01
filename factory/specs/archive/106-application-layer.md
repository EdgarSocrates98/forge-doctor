---
id: 106-application-layer
title: ScanService application layer — CLI/MCP/LSP share one pipeline
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/test_mcp.py tests/unit/test_lsp.py -q
  - python -m pytest tests/integration -q
  - python -m pytest -x -q
  - python -m mypy src
---

# Context
CLI has full pipeline (context→plugins→checks→fingerprints→profile→policy→
suppressions→baseline→cache). MCP and LSP rebuild it manually — they
diverge (no policy, no suppressions, no plugin controls).

# Acceptance Criteria
- `forge_doctor/application/` (or core/service.py): ScanRequest(path, opts),
  ScanResult(report, suppressions, timings, cache_stats); ScanService.run()
  is THE pipeline — plugins, profile, policy, suppressions, baseline,
  fingerprints, cache all inside.
- CLI `_execute_scan` delegates to it. MCP scan_project uses it with
  plugins disabled by default. LSP uses it.
- MCP: `--root DIR` sandbox — tool path args must resolve inside root,
  else JSON-RPC error. `initialize` negotiates protocolVersion (echo
  client's if supported: legacy 2024-11-05 + modern revisions);
  `server/discover` accepted as alias for initialize.
- LSP: `pygls.uris.to_fs_path` for URIs; workspace root =
  workspace.root_path not file's parent; unsaved-buffer overlay for
  didChange (scan the buffer text, not stale disk); publish empty
  diagnostics to clear resolved findings; debounce rapid changes.
- Tests: MCP path escape refused; ScanService applies suppressions to MCP
  result; LSP uri→path handles %20/drive letters; stale diagnostics cleared.

# Constraints
- No new mandatory deps; pygls stays optional.
