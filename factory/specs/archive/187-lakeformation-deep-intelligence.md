---
id: 187
title: Lake Formation Deep Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/test_lakeformation.py tests/unit/adversarial/test_lakeformation.py -x -q
  - python -m forge_doctor_data lakeformation inspect <fixture>
  - python -m forge_doctor_data knowledge verify
---

# Phase 6 - Lake Formation Deep Intelligence

## Context

Phase 6 of the ten-phase Forge Doctor Data program (`prompt_evo_next_step.md`):
turn Lake Formation from dispersed signal into a first-class model. Existing
surface: `checks/lakeformation.py` (LF000-002) + `knowledge/lakeformation/errors.json`.

## Acceptance Criteria

- `LakeFormationProjectModel` covering principals, admins, databases, tables,
  columns, data_locations, resource_links, grants, lf_tags, filters,
  cross_account, ram_shares, fgac, fta, hybrid_access.
- Sources: Terraform `aws_lakeformation_*`/`aws_glue_catalog_*`/`aws_ram_*`/
  `aws_iam_*`, CloudFormation `AWS::LakeFormation::*`/`AWS::Glue::*`/
  `AWS::RAM::*`, boto3 `lakeformation`/`glue` call-sites (grant_permissions,
  batch_grant_permissions, register_resource, create_database TargetDatabase).
- Cross-account model: producer account (target_catalog / grant principals),
  consumer account (ram_principal_association / bare account ids), resource
  links, IAM policy actions containing `lakeformation:`.
- FGAC/FTA is capability-driven (`knowledge/capabilities/lakeformation.json`),
  not hardcoded per-version behavior in checks.
- New checks LF010-LF018: no-data-lake-settings, IAMAllowedPrincipals defaults,
  dangling resource link, external grant without RAM, unregistered data
  location, LF-TBAC coverage, unused data-cells filter, hybrid overlap,
  external grant-option escalation.
- Platform graph integration: principals + GOVERNS edges + resource links.
- CLI `forge-doctor-data lakeformation`: inspect, permissions, graph,
  cross-account, compatibility.
- Deterministic, offline-only, no AWS calls.

## Constraints

- Match existing check/CLI/pack conventions (schema-2 packs, CheckBase).
- Evidence-gated; conservative on out-of-repo facts (producer RAM share).
