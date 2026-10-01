---
id: 109-ci-cache-test-fix
title: CI green - fix stale cache test invalidated by CI cache default
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_cache.py -q
  - python -m pytest -x -q
---

# Context
Last main CI run: 402 passed / 1 failed on Python 3.11/3.12/3.13, all on
`test_dep_change_invalidates_dependent`. Since spec 103, `scan_cache()`
defaults cache OFF when CI env vars are set (`in_ci()`), so in GitHub
Actions the second `ProjectContext` re-parses and `job2.fresh` is True
instead of False. The production code is behaving per the new contract;
the test is stale.

# Acceptance Criteria
- `test_dep_change_invalidates_dependent` constructs contexts with
  `ScanOptions(use_cache=True)` so cache behavior is deterministic
  regardless of CI/GITHUB_ACTIONS env vars.
- Any other test that depends on the incremental cache does the same.
- Full suite passes with `CI=true` set AND unset (prove both).
- No production code changes.

# Constraints
- Test-only change. Keep the auto-off-in-CI rule untouched.
