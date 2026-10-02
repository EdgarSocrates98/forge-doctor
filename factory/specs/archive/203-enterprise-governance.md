---
id: 203
title: Enterprise Governance
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k governance -x -q
---

# Roadmap-3 Phase 2 - Enterprise Governance

## Context

Organizations need layered policy control: org bundles → repo policies →
team exceptions → scan evidence. We have policy packs (P7),
suppressions with owner/expiry, and baselines. Missing: pack layering,
approval semantics, compliance reporting, per-branch baselines.

## Acceptance Criteria

- Policy pack layering: packs can `extends` another pack path/name;
  repo packs merge over org packs; deterministic precedence (repo >
  org > bundled). Cycles rejected with POLICY errors.
- Suppression `approved_by` field + `suppressions` report shows
  APPROVED/UNAPPROVED columns; policy pack can require approval
  (`require_approval = true`) making unapproved suppressions emit
  findings.
- `forge-doctor policy report` — compliance report: packs evaluated,
  violations by rule, suppression audit status, drift summary. text +
  JSON output.
- Per-branch baselines: `--baseline` resolves
  `.forge-doctor/baselines/<branch>.json` when given a name instead of a
  path (`--baseline main`), so CI gates per branch/environment.
- Evidence export: `scan --evidence-out <dir>` writes a dated evidence
  bundle (report + suppressions audit + packs used) for retention.

## Constraints

- All file formats versioned; invalid layering/approval configs emit
  findings, never silent drops.
- No auth model — approval is a declared field audit, not enforcement.

## Open questions

- Whether org bundles distribute via git repo URL, package data, or
  mounted path — implement local-path `extends` only; remote source
  deferred.
