---
id: 205
title: Historical Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k history -x -q
---

# Roadmap-3 Phase 4 - Historical Intelligence

## Context

Snapshots over time turn the scanner into architecture observability:
new/resolved risks, drift, capability drift, debt trend. Baselines
already diff findings; history needs storage, series, and trend math.

## Acceptance Criteria

- `forge-doctor scan --record` — append a compact snapshot to
  `.forge-doctor/history/<utc-timestamp>.json`: summary counts, finding
  fingerprints+ids, entity census, capability states, contract drift.
- `forge-doctor history` — list snapshots; `history diff <a> <b>` or
  `history diff --last` — new/resolved findings (fingerprint-stable),
  entity adds/removals, capability transitions, ARCH drift changes.
- `forge-doctor history trend` — per-category finding counts over the
  series + debt trajectory; deterministic text table + `--format json`.
- History dir is user-visible, git-ignorable, and never written during
  normal scans (only `--record`).
- Retention: `--keep N` prunes oldest snapshots deterministically.

## Constraints

- Snapshots are additive records; history commands never scan.
- Fingerprint-stability is the join key — must reuse existing
  fingerprint/diff machinery, not a new identity scheme.

## Open questions

- Whether history belongs in-repo (`.forge-doctor/history`) or user
  cache — default in-repo for CI artifact upload; cache opt-in.
