---
id: 209
title: Experiment / Validation Engine
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k experiment -x -q
---

# Roadmap-3 Phase 8 - Experiment / Validation Engine

## Context

Forge Lab verifies detection; it should also validate *remediation
hypotheses*: apply a candidate change to a fixture copy, re-run, compare
before/after (findings, metrics, file stats) — never in production.

## Acceptance Criteria

- `forge-doctor lab experiment <scenario> --hypothesis <name>`:
  copies the scenario fixture, applies a deterministic named transform
  (e.g. `bump-glue-version`, `partition-data`, `add-checkpoint`,
  `increase-trigger-interval`), rescans, and reports a before/after
  comparison: findings resolved/introduced, metric deltas, file counts.
- Hypotheses are a registry — named, declarative transforms over the
  fixture tree (same machinery as safe-fix transforms where possible).
- Output shows verdict: `improved | regressed | neutral` with reasons;
  `--format json` emits both snapshots + deltas.
- Experiments never touch the original fixture; temp copies only.
- Tests: a hypothesis resolving an expected finding reports `improved`;
  a no-op hypothesis reports `neutral`; unknown hypothesis errors.

## Constraints

- Deterministic transforms only — no live runtime measurement; "cost
  proxy"/throughput claims limited to static file/batch math.
- Lab fixtures stay ground-truth clean; experiments write to tmp.

## Open questions

- Whether hypotheses should be expressible in expected.json
  (`hypothesis:` key) for suite-level validation — nice-to-have.
