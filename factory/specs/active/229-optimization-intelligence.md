---
id: 229
title: Optimization Intelligence (deterministic improvement candidates)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k optimization -x -q
---

# Consolidation 5/5 - Optimization Intelligence

Roadmap order: after spec 228, before roadmap-4 wave 1. Numbered 229
because 212–224 are taken; it is the last consolidation before
warehouse coverage expansion.

## Context

The experiment engine (209) can already measure a named hypothesis
against a lab fixture, and what-if (191) evaluates declared changes.
Optimization intelligence closes the loop the other direction: read the
twin + findings, enumerate *candidate* optimizations the platform is
eligible for, estimate each with static cost proxies, and hand the
operator a validation path (`lab experiment`, `what-if`). Suggest and
estimate — never apply.

## Acceptance Criteria

- `core/optimize.py` — deterministic candidate enumeration over the
  twin/report: each candidate names the optimization (reusing the
  experiment-hypothesis vocabulary where overlap exists: partition,
  checkpoint, compaction, caching, trigger interval, glue version,
  …), the entities it applies to, eligibility evidence (why this
  entity qualifies — e.g., unpartitioned dataset with observed write
  amplification finding), a static cost-proxy estimate, and confidence
  (`low|medium|high` derived from evidence planes present).
- `forge-doctor optimize <path>` — ranked candidate list with the same
  citation discipline as `advise`; `--format json` stable shape.
  Rows are deduplicated per (entity, optimization) pair.
- Validation handoff: each candidate prints the exact `lab experiment
  <scenario> --hypothesis <name>` or `what-if` invocation that would
  validate it — no implicit execution.
- Candidates absent when evidence is absent — an undiagnosed entity
  produces no optimization claims (`unknown` stated where material).
- Tests: planted fixtures yield expected candidates; deterministic
  ordering; no candidates on clean fixtures.

## Constraints

- Estimates are static proxies labeled as such — never live cost or
  runtime numbers (no cloud calls, no target execution).
- Overlap with `advise` is intentional but disjoint: `advise` ranks
  *problems*; `optimize` ranks *opportunities*. Do not merge them.
- New optimization kinds are additive vocabulary + registry entries.

## Open Questions

- Whether candidates should dedupe against already-planned
  remediations/migrations — likely yes via fingerprint overlap; decide
  during implementation.
- Cost-proxy calibration source — docs-only tables vs observed
  metadata; keep observed-metadata-only until a stale-pricing policy
  is decided (same open question as spec 212).
