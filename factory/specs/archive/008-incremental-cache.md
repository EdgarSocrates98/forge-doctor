---
id: 008-incremental-cache
title: Incremental analysis cache + watchfiles watch
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_cache.py -q
---

# Acceptance Criteria
- .forge-doctor/cache/ stores per-file sha256 -> analyzer buckets; unchanged files reuse findings
- invalidated on content change; dependents not needed for v1 (per-file granularity)
- scan --cache/--no-cache flag; `forge-doctor cache` shows stats / cache clean
- watch uses watchfiles when installed ([watch] extra), else current snapshot polling
- .forge-doctor/ added to scaffold gitignore + default traversal excludes
