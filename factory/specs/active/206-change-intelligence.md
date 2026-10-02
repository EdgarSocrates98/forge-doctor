---
id: 206
title: Change Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "semantic_diff or change_intel" -x -q
---

# Roadmap-3 Phase 5 - Change Intelligence

## Context

`diff --semantic` does entity diff + blast radius + risk. Full change
intelligence adds capability diff (what the platform gains/loses) and
migration requirements for detected version moves.

## Acceptance Criteria

- `diff --semantic` report gains a **capability diff** section:
  capabilities whose status transitions between refs (e.g.
  `ICEBERG_MERGE_WRITE: unsupported -> supported`), computed by
  evaluating the capability registry against each ref's observed env.
- **Migration requirements**: when entity attrs show a version move
  (glue_version, dbr_version, format_version, runtime…), the report
  links the matching named migration plan's blockers/warnings.
- **Requirement extraction**: per changed entity, the plan steps that
  apply (e.g. `glue 5->6` pulls `glue-4-to-5`-style required changes).
- JSON output gains `capabilities` + `migration_requirements` keys;
  exit code semantics unchanged (risk still gates).
- Adversarial: no version move → empty sections; unknown versions →
  `unknown` transitions, never fabricated.

## Constraints

- Reuse `evaluate_change`/`plan_migrations`/capability registry — no
  parallel compat logic.
- Plan-only; never mutates; deterministic ordering.

## Open questions

- None blocking; plan matching is by name convention between
  observed attr moves and `migrate` plan ids.
