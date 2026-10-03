# Run: 134-terraform-model (Terraform stage 1)

- Spec: `factory/specs/active/134-terraform-model.md` (staged, grill: completed)
- Agent: devin
- Source prompt: `prompt_evo_terraform.md` — 8-stage Terraform Intelligence
  program (model → modules/providers → state → plan → tests/CI → AWS
  platform → Databricks → cross-domain graph).

## Implemented

- `TerraformProjectModel` (`analyzers/terraform_model.py`) — extends the
  hcl_lite brace-scanner to every top-level block: `terraform` internals
  (required_version, required_providers incl. nested `aws = { ... }` form,
  backend), provider (alias/region), module (source/version), resource,
  data, variable, output, locals, moved/import/removed/check; plus a
  reference graph (`aws_iam_role.glue`, `module.vpc`, `var.env` edges).
- 8 checks (`category = "terraform"`): TF000 anchor, TF001 missing
  required_version, TF002 provider without constraint (block or
  requirement), TF003 unbounded `>=` ( `~>` counts as bounded), TF020
  local module, TF021 unpinned registry module, TF022 mutable git ref
  (none/`ref=main|...`), TF130 local backend.
- `forge-doctor-data terraform inspect` — Terraform version / Providers (+alias)
  / Resources by domain / Modules local-vs-remote / State backend /
  reference count / severity-sorted findings. Verified live on a fixture.
- `knowledge/terraform/{language,providers}.json` — feature floors
  (moved≥1.1, check≥1.5, import≥1.5, test≥1.6), provider source map +
  major floors.
- Inbox specs written for stages 2-8: 135 modules/providers+lockfile,
  136 state/backend/workspace, 137 plan JSON + blast-radius + plan-diff,
  138 tests/CI, 139 AWS data platform, 140 Databricks+ownership,
  141 cross-domain graph.

## Latent quirks found

- `hcl_lite._ATTR_RE` strips `//.*` as a comment — butchers quoted URLs
  (`"git::https://…?ref=v1.2.3"` → `"git::https:`). Checks read `source`
  via a raw-body quoted regex (`_module_source`) instead of the attr.
- `~> 5.0` has an implicit upper bound (`<6.0`) — TF003 treats `~>` as
  bounded regardless of component count.

## Verification

- `pytest`: 571 passed (+26)
- `ruff check`, `ruff format --check`: clean
- `mypy src`: clean (80 files)

## Gate

Spec left in `active/` — awaiting human review + `archive --accepted`.
