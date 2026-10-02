---
id: 207
title: Continuous Incremental Analysis
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k incremental -x -q
---

# Roadmap-3 Phase 6 - Continuous Incremental Analysis

## Context

`--cache` + `--watch` exist, but every scan still evaluates all checks.
For IDE, fast CI, and monorepos we need: file changed → semantic facts
changed → only impacted rules re-evaluated.

## Acceptance Criteria

- A rule→evidence dependency map: each check declares (or derives) the
  evidence domains it reads (files, pyproject, tf, models, runtime
  artifacts); a `RuleDeps` registry records them.
- Incremental scan path: given changed files (from `--files` or watch
  events), compute the affected check set = checks whose evidence domains
  intersect the change + checks consuming derived models whose inputs
  changed. `--stats` reports `skipped-unchanged` counts.
- Correctness invariant: incremental output ≡ full-scan output on the
  same tree — a test compares incremental vs full results on fixtures
  after seeded edits. Where a check's evidence domain can't be bounded,
  it always runs (conservative default).
- `forge-doctor scan --incremental` flag (opt-in; full scan stays
  default) and `--watch` uses the incremental path when enabled.

## Constraints

- Correctness over speed: when in doubt, run the check.
- The dependency map must be testable: every check in the registry has
  a declared domain set; a test fails on undeclared checks (or derives
  them from the model readers they call).

## Open questions

- Deriving domains automatically (instrument ctx/model access) vs
  declared constants — start declared + a test asserting coverage;
  derive later if drift appears.
