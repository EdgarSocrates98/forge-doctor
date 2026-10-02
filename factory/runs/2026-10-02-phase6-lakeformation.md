---
run: phase6-lakeformation-deep-intelligence
date: 2026-10-02
spec: factory/specs/active/187-lakeformation-deep-intelligence.md
status: built
---

# Phase 6 — Lake Formation Deep Intelligence

## Built

- `analyzers/lakeformation_model.py` — `LakeFormationProjectModel` with
  principals, admins, databases, tables, columns, data_locations,
  resource_links, grants, lf_tags, filters, ram_shares/principals,
  external_accounts, iam_lf_actions, and the fgac/fta/hybrid_access flags.
  Sources: Terraform + CloudFormation IaC, boto3 `lakeformation`/`glue`
  call-sites (AST-based client bindings; dict kwargs via `ast.literal_eval`),
  IAM policy bodies for `lakeformation:`/`glue:` actions.
- Nested-block extraction (`_blocks`) for HCL sub-blocks
  (`resource {}`, `table_with_columns {}`, `create_*_default_permissions`,
  `table_data {}`, `row_filter {}`) since `hcl_lite` flat attrs miss them.
- Cross-account resolution: bare 12-digit principals are account-level grants;
  IAM-role ARNs flag external only when their account differs from every
  locally-inferable account (admins, registration roles) — the producer-side
  RAM share is out-of-repo and never claimed missing.
- `checks/lakeformation.py` — LF010-LF018 added (existing LF000-002 kept):
  no settings (010), IAMAllowedPrincipals defaults (011), dangling resource
  link (012), external grant without RAM association (013), unregistered
  data location (014), LF-TBAC tag coverage (015), unused cells filter (016),
  hybrid overlap (017), external grant-option (018).
- `knowledge/capabilities/lakeformation.json` — FGAC per engine
  (glue 3/4 conditional, 5 supported; athena v3 supported; emr 5.32+
  conditional), FTA, hybrid access, cross-account (RAM + CROSS_ACCOUNT_VERSION 3).
- `knowledge/lakeformation/{permissions,cross-account,hybrid}.json` —
  schema-2 domain packs.
- `cli/lakeformation.py` — inspect, permissions, graph, cross-account,
  compatibility (per-version capability matrix), findings.
- `platform_graph_builder._lakeformation` — principal entities +
  GOVERNS edges (catalog/data-location), registered locations as
  storage entities, resource links as DEPENDS_ON edges to the producer
  catalog.
- Fix folded in: `dynamodb_model._tf_stream_consumers` guarded against
  label-less TF blocks (IndexError on `terraform {}`/`locals {}` blocks).

## Bugs fixed during build

- `_blocks` string-skip left `i` on the closing quote — it was re-entered as
  an opening quote and swallowed the next block header. `i += 1` after the
  inner while fixes it.
- `_IAM_PRINCIPALS_RE` missed Terraform's `IAM_ALLOWED_PRINCIPALS` spelling.
- Cross-account flagging required a post-pass over local accounts so
  same-account role ARNs stop flagging as external.
- boto3 `grant_permissions(Resource={...})` dict kwargs aren't string
  literals — extracted from the AST node directly.

## Verification

- `pytest`: 1071 passed
- `mypy src`: clean (122 files)
- `ruff check` + `ruff format --check`: clean (220 files)
- `forge-doctor lakeformation inspect/permissions/graph/cross-account/
  compatibility/findings` on a multi-resource fixture — admins, grants
  (columns/database/catalog kinds), resource link, external accounts,
  hybrid flag all render correctly.
- `forge-doctor knowledge verify`: all packs ok.

## Review notes

- LF012 semantics: flags links unreferenced by any in-repo grant. The
  producer-side RAM share is out-of-repo by construction, so no negative
  claim is made about it.
- LF001 (older check) scans for RAM tokens; LF012 uses the model. Both kept —
  LF001 is the coarse net, LF012 the precise one.
