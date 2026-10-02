---
id: 186
title: Architecture contract + drift detection
agent: devin
risk: medium
verification: pytest tests/unit/test_contract.py tests/unit/adversarial/test_contract.py
program: prompt_evo_next_step phase 5
---

# Context

Phases 1-4 reason over what exists. Phase 5 adds the *desired* plane: an
optional `platform-contract.yml` the project owns, compared against
declared (IaC), implemented (code), and runtime (artifacts) planes.

# Acceptance criteria

- `core/contract.py`: versioned `PlatformContract` (contract_version,
  pipelines, datasets, governance.allowed_dependencies, per-pipeline
  compute/storage/orchestration/sla/semantics/ownership/capabilities).
- YAML via `pyyaml` when installed, else a strict minimal parser covering
  the documented contract shape — issues reported, never raised.
- `ArchitectureDrift` (expected/observed/source/entity/drift_type/
  severity) + `detect_drift(ctx, contract, runtime=[])`.
- ARCH001 platform mismatch (code/IaC/runtime), ARCH002 version drift,
  ARCH003 storage-format drift, ARCH004 undeclared dependency,
  ARCH005 SLA runtime violation (identity-joined), ARCH006 idempotency
  contract without evidence, ARCH007 ownership conflict (terraform vs
  manual boto3 create_*), ARCH008 feature outside approved capabilities.
- `checks/architecture.py` emits ARCH### as scan checks when a contract
  exists; `cli/contract.py` adds `contract validate <file>` and
  `architecture drift . [--runtime ...] [--contract ...] [--json]`.
- No contract = no drift (never a violation). Missing runtime = runtime
  checks unevaluated.

# Constraints

- Offline only; ownership keyed on (service, cloud-name) exact matches.
- SLA joins require exact pipeline-name identity in the artifact.

# Review notes

The minimal YAML parser covers nested mappings, scalars, block/inline
lists and inline `{k: v}` maps; exotic YAML reports a parse issue.
