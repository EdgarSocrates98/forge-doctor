---
id: 001-fingerprint-v3
title: Semantic fingerprint v3
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_models.py tests/unit/test_finding_v2.py tests/unit/test_baseline.py -q
  - python -m ruff check src
---

# Context
Current fingerprint = sha256(check_id|file|line|column|message) — line moves and
message edits create phantom "new" findings, breaking baseline/diff.

# Acceptance Criteria
- fingerprint material: check_id | relative_file | symbol_anchor | evidence_anchor (+occurrence index on duplicates)
- line/column/message NOT part of identity
- enclosing function/class symbol path used when resolvable (Python AST); normalized AST statement dump or normalized source line otherwise
- duplicate identical anchors disambiguated by stable ordinal (post-pass in runner)
- CheckResult carries fingerprint_version=3 concept (module constant FINGERPRINT_VERSION=3)
- BASELINE_FORMAT bumped to 2; loader keeps reading v1 baselines (they simply won't match — documented)
- tests updated: line move stable, message edit stable, file change unstable

# Constraints
- No new runtime deps. Deterministic across platforms (posix paths).
