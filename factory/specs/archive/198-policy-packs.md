---
id: 198
title: Organization Policy Packs
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_policy_pack.py tests/unit/adversarial/test_policy_pack.py -x -q
  - python -m forge_doctor policy list --path <repo>
  - python -m forge_doctor policy eval --path <repo>
  - python -m forge_doctor policy validate <pack.yml>
---

# Roadmap-2 Phase 7 - Organization Policy Packs

## Context

Teams need org rules no built-in check knows: "no public RDS",
"every bucket has tags", "CODEOWNERS must exist", "no password
literals". This phase makes them data — declarative `forbid`/`require`
packs — not plugin code.

## Acceptance Criteria

- `core/policy_pack.py`: `PolicyPack{name, version, rules}`,
  `PolicyRule{id, severity, message, forbid|require, recommendation}`.
- Discovery: `.forge-doctor/policy/*.yml|*.yaml|*.json`, root
  `policy.yml`/`org-policy.yml`, plus `[tool.forge-doctor]
  policy_packs = [...]` paths.
- Rule kinds:
  - `forbid.pattern` + `file_glob` — finding per matching line;
  - `forbid.terraform {resource_type, attr, op, value}` — ops
    `equals`/`matches`/`present`;
  - `require.file` — finding when zero files match the glob;
  - `require.file_glob` + `contains` — finding per matching file
    missing the regex;
  - `require.terraform {resource_type, attr, op}` — finding per
    resource lacking/failing the attr condition.
- `PolicyPackError` on malformed schema, duplicate ids, bad regex,
  bad severity; unloadable packs surface as `POLICY010` error findings
  inside scans, never silent.
- `checks/policy_pack.py` `OrgPolicyPacks` (id `POLICY010`, category
  `policy`) — runs all packs inside normal scans; findings carry the
  org rule ids so severity policy + suppressions govern them.
- `forge-doctor policy list|eval [--format json]|validate <file>`.
- Mini-YAML extended to parse `- key: value` list-of-mapping items so
  packs work without PyYAML.
- Tests: each rule kind, discovery paths, pyproject config, malformed
  inputs, determinism, `**/` root-level matching, bool attr compare.

## Constraints

- `**/` glob matches root-level files too (fnmatch `**/` otherwise
  requires a subdirectory).
- Terraform bool attrs compare case-insensitively (`True` vs `"true"`).
- No packs configured → check emits nothing (opt-in).

## Review Notes

- `_mini_yaml` gained list-of-mapping item support — a superset used
  by contracts too; existing contract parsing unchanged (subset).
