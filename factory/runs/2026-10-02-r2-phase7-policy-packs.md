# Roadmap-2 Phase 7 run — Organization Policy Packs

- Spec: `factory/specs/active/198-policy-packs.md`
- Commit message: `feat(policy): add organization policy packs`

## Implemented

- `core/policy_pack.py` — `PolicyPack` / `PolicyRule` /
  `ForbidRule` / `RequireRule`; `load_pack`, `discover_packs`
  (`.forge-doctor-data/policy/*`, `policy.yml`, `org-policy.yml`,
  `[tool.forge-doctor-data] policy_packs`), `load_packs` (invalid →
  `POLICY010` error finding), `evaluate_packs`.
- Rule kinds: `forbid.pattern`+`file_glob` (per-line regex),
  `forbid.terraform{resource_type,attr,op,value}` (`equals`/`matches`/
  `present`), `require.file`, `require.file_glob`+`contains`,
  `require.terraform{attr,op}`.
- `checks/policy_pack.py` `OrgPolicyPacks` (`POLICY010`, category
  `policy`) — packs run inside normal scans; findings carry org rule
  ids so severity policy and suppressions govern them. Zero packs →
  zero results (opt-in).
- `cli/policy.py` — `policy list`, `policy eval [-f json]`
  (exit 1 on violations), `policy validate <file>`.
- `config.py` — `policy_packs` key.
- `contract.py` `_mini_yaml` — `- key: value` list-of-mapping items
  now parse (packs don't need PyYAML); `_looks_like_mapping_item`
  keeps `scheme://` scalars safe. Superset — contract parsing unchanged.
- Glob fix: `**/` patterns match root-level files (`_glob` helper).
- TF bool attrs compare case-insensitively in `equals`.

## Verified

- Fixture: `aws_db_instance` public + untagged bucket + missing
  CODEOWNERS + `password = "hunter2"` → ORG001-004 all fire with
  correct severities and `file:line`.
- `pytest tests/unit/test_policy_pack.py
  tests/unit/adversarial/test_policy_pack.py` → 19 passed (schema,
  malformed YAML → POLICY010, duplicate ids, bad regex/severity,
  case-insensitive bool, zero-match require, determinism).
- `forge-doctor-data policy list|eval|validate` smoke-tested; findings flow
  through `scan` under the `policy` category; `explain POLICY010`
  resolves.

## Open questions

- Pack *distribution* (git submodule, org package, URL fetch) is
  deliberately out of scope — packs are local files only; open
  question is whether orgs need a shared-pack discovery mechanism.
- `require.terraform` on `resource_type: "*"` checks every TF resource
  including data sources — whether data sources should be excluded is
  a pack-authoring question, not a product decision.
