---
id: 210
title: Knowledge Supply Chain
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k knowledge -x -q
---

# Roadmap-3 Phase 9 - Knowledge Supply Chain

## Context

Mutable knowledge (versions, compat matrices, error signatures) ships in
packs. Updating AWS/Databricks facts shouldn't require manual edits
across dozens of checks — there needs to be an authoring/validation/
publish pipeline for packs.

## Acceptance Criteria

- `forge-doctor knowledge new <domain>` — scaffold a pack with correct
  schema_version, provenance fields, and examples — same discipline as
  `plugins init`.
- `forge-doctor knowledge diff <a> <b>` — semantic pack diff: entries
  added/removed/changed (versions, statuses, capability keys), not text
  diff.
- `forge-doctor knowledge test` — pack conformance suite: every pack
  validates against its schema, capability assertions evaluate
  consistently (sample contexts), error-signature regexes compile and
  have ≥1 positive fixture match.
- `knowledge publish` checklist — validates pack freshness fields
  (`verified_at`, `sources`), bumps pack_version, emits the publish
  summary. Dry-run validation only — actual distribution is manual.
- Tests: pack scaffold loads + verifies; diff detects add/remove/change;
  conformance catches a broken regex and a missing fixture match.

## Constraints

- All local/deterministic; no doc scraping or network fetch in v1 —
  supply-chain *fetch* automation is explicitly deferred.
- Packs remain data; engine code untouched.

## Open questions

- Automated source fetching (AWS docs → pack PR)? Deferred — needs a
  network policy decision.
