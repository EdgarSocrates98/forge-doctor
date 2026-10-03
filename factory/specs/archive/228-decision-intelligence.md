---
id: 228
title: Decision Intelligence (ranked, explainable actions)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k decision -x -q
---

# Consolidation 4/5 - Decision Intelligence

Roadmap order: after spec 227, before roadmap-4 wave 1. Numbered 228
because 212–224 are taken.

## Context

The engine already produces root-cause clusters, remediation plans,
safety-classified fixes, policy findings, and graph blast radius — but
the operator still assembles the "what do I do first" answer by hand.
Decision intelligence merges those existing signals into one ranked,
fully-cited action list. Automate ranking, not decisions: every
recommendation cites its evidence.

## Acceptance Criteria

- `core/decisions.py` — deterministic ranking over existing artifacts:
  findings (severity, confidence), root-cause clusters (cluster size),
  remediation plans + fix safety classes, policy violations, and blast
  radius (graph traversal depth/fan-out per entity). The scoring
  function is documented, pure, and total — identical inputs always
  produce identical ranks.
- `forge-doctor-data advise <path>` — the ranked action list: each row
  carries the action, its rank score + breakdown, the finding
  fingerprints and entity ids backing it, the safety class of any
  applicable fix, and open unknowns. `--format json` stable shape;
  `--top N` truncation after deterministic ordering.
- Ties break deterministically (score, then fingerprint order).
- Explainability: `advise` rows link back to `explain <check-id>` and
  `trace` targets — no recommendation without a citation.
- Tests: fixture with known findings produces a stable ranked list;
  score breakdown fields sum to the reported score; empty projects
  produce an empty list with exit 0.

## Constraints

- Ranking consumes only existing signals — no new check semantics, no
  severity redefinition, no invented urgency.
- No LLM, no learned weights; weights are documented constants in the
  module, tunable via config only if a real need emerges (open question
  below).
- Advisory surface only — `advise` never applies fixes; it points at
  `fix`/`remediate` commands.

## Open Questions

- Whether score weights should be configurable via `forge-doctor-data.yml`
  — default no (documented constants keep behavior explainable);
  revisit if orgs need policy-weighted ranking.
- Whether `advise` should absorb `--baseline` new-finding prioritization
  or stay snapshot-scoped — decide against real usage.
