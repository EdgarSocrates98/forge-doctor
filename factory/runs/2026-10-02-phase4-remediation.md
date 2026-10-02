---
spec: 185-remediation-planning
phase: prompt_evo_next_step phase 4 (Deterministic Remediation Planning)
date: 2026-10-02
agent: devin
---

# Phase 4 — Deterministic Remediation Planning

## What was built

- `core/remediation.py` — `RemediationAction` (id, description,
  target_entity, rationale, expected_effect, validation, depends_on) and
  `RemediationPlan` (problem, actions, prerequisites, dependencies,
  risks, validation_steps, rollback_notes). `plan_remediation(results,
  clusters, root_cause=...)` loads `knowledge/remediation/` entries via
  `list_packs`, matches `check_id` findings (targets aggregated and
  sorted per check id) and `cluster` entries against FindingCluster id
  prefixes. `--root-cause` filters to cluster plans only.
- `knowledge/remediation/` — six schema-2 packs, all verified:
  `spark` (SPARK001/003/006/009/010), `parquet` (PARQ010/020/040/041/042),
  `iceberg` (ICE001/008/009/010/024/025), `streaming` (STREAM002/003/020/
  070), `platform` (PLAT001/003/007), `chains` (RC_STREAM_COMMITS 5-step
  required example, RC_SPARK_SKEW 4-step).
- `cli/remediate.py` — `forge-doctor remediate . [--root-cause <id>]
  [--json]`; footer states nothing is applied automatically.

## Boundaries

Advisory only: no patch generation, commits, Terraform, deployments, or
migrations. Unmapped check ids produce no plan. Deterministic ordering:
plans sorted by id, action order and `depends_on` come from the packs.

## Verification

- `pytest tests/unit/test_remediation.py tests/unit/adversarial/test_remediation.py`
  — 12 passed (pack provenance, aggregated targets, required chain
  ordering + deps, root-cause filtering, determinism, no-autoapply
  invariant, silent-unmapped).
- `pytest -x -q` — full suite green; `mypy`, `ruff`, format clean.
- `knowledge verify` — all remediation packs ok.
- CLI smoke on a repartition(1) project prints PLAN-SPARK003 /
  PLAN-PARQ010 with actions, risks, validation steps.

## Open questions

None. Pack coverage is additive — new check families get remediation
mappings as domains deepen (phases 6-9).
