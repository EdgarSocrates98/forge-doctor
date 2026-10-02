---
id: 224
title: Cross-platform migration intelligence + what-if
agent: claude
risk: high
verification:
  - python -m pytest tests/unit/ -k "migration or whatif" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 7 - Cross-platform migration

## Context

Doc wave 7 = the payoff of the abstraction layers (212, 223): migrate
*between* platforms, not just versions. `forge-doctor migrate plan
--from snowflake --to bigquery`, `what-if --change warehouse=bigquery`.
This depends on warehouse/abstraction models being real — it maps
entities via capability parity, lists required changes, and grades
confidence per translation.

## Acceptance Criteria

- `migrate plan --from <platform> --to <platform>` builds a plan:
  entity mapping table (snowflake warehouse→bigquery slots etc.),
  per-entity required changes (DDL dialect notes, feature gaps via
  capability packs), capability deltas (`lost`/`gained`/`equivalent`
  lists), ordered stages (catalog → schema → data → compute →
  consumers).
- `what-if --change platform=<target>` on the abstraction layer: which
  entities/services change kind, which rules would newly fire.
- `MIGR###` checks on plans: `MIGR001` feature with no target
  equivalent (hard blocker); `MIGR002` semantic difference needing
  manual review (e.g., time-travel window semantics); `MIGR003`
  unmapped consumers (downstream entities with no target link).
- Plans are *reports*, not executable deploys — deterministic plan
  document (json+text) like `remediate`.
- Lab scenario: snowflake→bigquery fixture end-to-end.

## Constraints

- Requires 212 + at least two warehouse adapters + 223 abstractions —
  do not start before those land; this spec documents the target shape.
- No auto-generated executable DDL claims — translations are advisory
  with confidence + source-evidence links.

## Open Questions

- SQL dialect translation depth: structural notes vs real transpilation
  (sqlglot-style)? Default: structural notes + callouts; transpiler
  integration deferred as a separate spec.
