---
id: 103-cache-hardening
title: Cache moves out of target + dependency-aware invalidation
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/test_cache.py -q
  - python -m pytest tests/unit/test_index.py tests/unit/test_lineage.py -q
  - python -m pytest -x -q
---

# Context
Cache lives at `<target>/.forge-doctor/cache/` — a malicious repo can ship
scan-cache.json with correct SHAs but fake facts (poisoning). Also facts are
only keyed by file sha: a changed imported module doesn't invalidate
dependents' semantic facts.

# Acceptance Criteria
- Cache dir = platform user cache: %LOCALAPPDATA%/forge-doctor/cache (win),
  ~/.cache/forge-doctor (linux), ~/Library/Caches/forge-doctor (mac). No new
  dep — stdlib resolution.
- Cache key namespace: sha of canonical repo root + tool version +
  analyzer-schema version constant + file sha.
- Cache auto-disabled when CI env var set (CI/GITHUB_ACTIONS), overridable
  via --cache; `cache` cmd reports location + can `clean`.
- Dependency-aware invalidation: per-file facts record imported module deps
  resolved to files + each dep's export signature; changed dep export sig →
  dependent file re-analyzed (facts recomputed), its own source not re-parsed
  unless its sha changed.
- Legacy in-project cache: ignored (not loaded) + `cache clean` removes it.
- Tests: poisoned cache file not trusted, dep change invalidates dependent.

# Constraints
- No network, stdlib only, deterministic. Watch/scan unchanged semantics.
