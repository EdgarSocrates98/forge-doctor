# Run: Phase 1 — Cross-Domain Rule Engine

- program: `prompt_evo_next_step.md` (commit 1 of 10)
- spec: `factory/specs/active/182-cross-domain-rules.md`
- head before: `bcc2980`

## Built

- `core/crossdomain.py`: `CrossDomainRule` (declared entity-kind /
  relationship / capability prerequisites + typed predicate),
  `ContributingFact`, `CrossDomainHit`, `RuleContext`,
  `rule_context(ctx)`; `CapabilityRegistry.capabilities()` added.
- `analyzers/airflow_model.py`: `AirflowTask.target` (external resource
  for orchestrating operators) + `AirflowDag.default_retries`
  (`default_args["retries"]` literal).
- `platform_graph_builder._airflow`: task→target INVOKES edges for
  Glue/Lambda/SFN/EMR/Databricks operators.
- `checks/platform_rules.py`: PLAT001–PLAT007, category `platform`,
  findings render contributing facts.
- `cli/platform.py`: `platform findings` (`--json`).

## Verification

- `pytest tests/unit/test_crossdomain.py + adversarial` → 26 passed
- `pytest -x -q` → 980 passed
- `mypy src` → clean (110 files)
- `ruff check` + `ruff format --check` → clean
- Dogfood: `platform findings` on the mandatory Airflow retries=5 →
  Glue job → Iceberg append fixture emits PLAT001 (DERIVED) with all
  four contributing facts; same finding appears in `scan` output.

## Decisions

- Append-style sink = SQL `INSERT` or v2 `writeTo` chain not ending in
  overwrite/createOrReplace (Iceberg model evidence, not AST re-walk).
- `batch_id` counts as dedup evidence only when used inside an Item/Key
  literal — a bare handler parameter is not idempotency.
- Absent `format-version` + row-level ops resolves UNSUPPORTED via the
  pack (catalog default is v1) — surfaced, not softened.
