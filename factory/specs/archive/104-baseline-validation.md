---
id: 104-baseline-validation
title: Baseline loader validates strictly — never silent-empty
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_baseline.py tests/integration -q -k baseline
  - python -m pytest -x -q
---

# Context
`load_baseline` swallows OSError/JSONDecodeError → returns empty set → every
finding becomes NEW. Wrong fingerprint_version baselines silently mismatch.

# Acceptance Criteria
- Missing file, unreadable file, invalid JSON → explicit BaselineError →
  scan exits 2 with clear stderr message.
- Baseline format/fingerprint_version mismatch → explicit error suggesting
  re-baseline (--save-baseline), exit 2.
- Malformed entries (missing fingerprint fields) → error, not empty set.
- Same behavior for `diff` when loading report files that aren't scan JSON.
- Tests cover: missing file, bad json, v1 fingerprint baseline, valid v3.

# Constraints
- Error messages actionable (mention re-baseline command).
