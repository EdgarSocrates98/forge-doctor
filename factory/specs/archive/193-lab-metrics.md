---
id: 193
title: Forge Lab Quality Metrics - Precision/Recall/Coverage
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_lab.py -x -q
  - python -m forge_doctor lab metrics
  - python -m forge_doctor lab metrics --json
---

# Roadmap-2 Phase 2 - Quality Metrics

## Context

Forge Lab (192) produces per-category comparisons. This phase turns
them into objective quality numbers: "98% of benchmark problems
detected, zero false positives" instead of "seems good".

## Acceptance Criteria

- `allowed_findings` in `expected.json` + lab-level `_defaults.json`
  allowlist — declared-known-benign detections never count as FPs.
  `extra` in reports only lists genuinely unreviewed detections.
- `core/metrics.py` — `MetricRow` per domain + TOTAL with:
  precision, recall, FPR (forbidden-hit/declared), parser coverage
  (AST-parsed .py / total .py), graph edge recall, capability accuracy,
  root-cause recall. `None` (rendered `-`) when the denominator is 0.
- FP candidates = undeclared, un-allowed findings at WARNING+;
  INFO anchors describe surface and never count.
- `forge-doctor lab metrics [--labs dir] [--json]`.
- Tests for: perfect run (100/100/0), missed → recall drop,
  forbidden hit → FPR, defaults merging, zero-denominator → None/0.

## Constraints

- Metrics must be honest: no padding denominators, no dropping extras
  silently (they stay visible in `lab run` output).
- Deterministic ordering (sorted domain rows, TOTAL last).

## Review Notes

- Precision against non-exhaustive truth understates reality — the
  allowlist mechanism is how ground truth declares "reviewed noise".
