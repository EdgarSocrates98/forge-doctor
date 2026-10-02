---
id: 185
title: Deterministic remediation planning
agent: devin
risk: medium
verification: pytest tests/unit/test_remediation.py tests/unit/adversarial/test_remediation.py
program: prompt_evo_next_step phase 4
---

# Context

Phase 3 clusters findings into root causes. Phase 4 turns findings and
clusters into ordered, deterministic remediation plans — advisory only.

# Acceptance criteria

- `core/remediation.py` provides `RemediationPlan` (problem, actions,
  prerequisites, dependencies, risks, validation_steps, rollback_notes)
  and `RemediationAction` (id, description, target_entity, rationale,
  expected_effect, validation, depends_on).
- `knowledge/remediation/` packs map finding classes and root-cause
  chains to remediation candidates; schema-2 provenance (pack_version,
  verified_at, sources); passes `knowledge verify`.
- Chain plan for RC_STREAM_COMMITS orders: adjust streaming trigger →
  review output partitioning → compact data files → expire snapshots →
  re-run consumer metrics (with depends_on edges).
- CLI: `forge-doctor remediate .` and `forge-doctor remediate
  --root-cause <id> .` (`--json`).
- No automatic patch/commit/deploy/Terraform/migration. Output states
  this explicitly.
- Deterministic ordering of plans and actions.

# Constraints

- One plan per check id (targets aggregated, sorted); one plan per
  matched cluster chain.
- Findings without a remediation mapping produce no plan (no guessing).

# Review notes

Pack coverage: spark, parquet, iceberg, streaming, platform families +
the two root-cause chains. Remaining check families can get packs in
later phases as they gain remediation guidance.
