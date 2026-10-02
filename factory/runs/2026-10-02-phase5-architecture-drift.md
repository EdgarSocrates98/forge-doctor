---
spec: 186-architecture-contract-drift
phase: prompt_evo_next_step phase 5 (Architecture Contract + Drift)
date: 2026-10-02
agent: devin
---

# Phase 5 — Architecture Contract + Drift

## What was built

- `core/contract.py` — versioned `PlatformContract` schema
  (contract_version; pipelines with compute.platform/version,
  storage.format, orchestration engine, sla.duration, semantics.idempotent,
  ownership.owner, capabilities.approved; datasets; governance.
  allowed_dependencies). Loads via `pyyaml` when installed, else a strict
  minimal parser covering the documented contract shape (nested mappings,
  scalars, block + inline lists, `{k: v}` flow maps); malformed files
  produce issues, never exceptions.
- `ArchitectureDrift` (check_id, drift_type, entity, expected, observed,
  source, severity, evidence_kind) + deterministic `id` digest.
- `detect_drift(ctx, contract, runtime)` implementing ARCH001-008:
  - ARCH001 platform mismatch across code/IaC and runtime artifacts
    (adapter source → platform family).
  - ARCH002 version drift (contract vs Terraform `aws_glue_job`
    glue_version and code-level `glue_version=` kwargs).
  - ARCH003 storage-format drift (contract vs implemented writes).
  - ARCH004 dependencies observed but absent from
    `governance.allowed_dependencies`.
  - ARCH005 SLA violations — requires exact pipeline-name identity in
    the artifact (identifiers or execution ids); no identity, no
    violation.
  - ARCH006 `idempotent: true` contract vs append-only write evidence.
  - ARCH007 ownership: terraform resource names ∩ boto3 `create_*` calls
    bound to `boto3.client("<service>")` receivers, keyed on
    (service, cloud-name).
  - ARCH008 features/dependencies outside `capabilities.approved`.
- `checks/architecture.py` — ARCH### registered as scan checks (category
  `architecture`); fire only when a valid contract exists.
- `cli/contract.py` — `contract validate <file>` and
  `architecture drift . [--contract f] [--runtime a.json] [--json]`.
- `ctx.contract` lazy accessor + auto-detect at project root.

## Evidence policy

No contract → no drift. Missing config/version/runtime evidence →
UNRESOLVED (silent), never a fabricated violation. ARCH005 joins on
exact pipeline identity only.

## Verification

- `pytest tests/unit/test_contract.py tests/unit/adversarial/test_contract.py`
  — 22 passed (parse paths, mini-YAML edge cases, every ARCH rule,
  identity-gated SLA, determinism).
- Full suite + mypy + ruff + format clean.
- CLI smoke: contract declaring glue@5.1/iceberg over a project with
  glue_version=4.0 TF resource, a boto3 `create_job` for the same name,
  and a DynamoDB table → ARCH002 + ARCH004 + ARCH007 printed with
  expected/observed/source fields.

## Open questions

- Ownership currently covers terraform ∩ manual-create pairs; Airflow
  provisioning operators could join as a third owner family later.
- ARCH008 is feature-presence based; per-feature capability evaluation
  (e.g., ICE merge on glue 4.0) stays in PLAT002 territory.
