# Run: Program P wave 3 — Cost driver intelligence (spec 237)

- **Commit**: this commit
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/237-cost-driver-intelligence.md`

## Scope

Technical cost-driver taxonomy over the spec-235 execution model —
bytes, slot-ms, executor time, replica counts — never billing or
prices.

## Files changed

- `src/forge_doctor_data/core/cost_drivers.py` — **new**: `CostDriverKind`
  (17 kinds), `CostDriver`, `extract_drivers` (execution + entity
  evidence, per-engine time/cpu kind mapping), attribution chain
  execution→dataset→team→environment (team only with ownership
  evidence), `DataTransferSignal`/`detect_transfers` (cross-cloud only
  when both clouds are known), `cost_findings` COST001–COST007,
  `driver_kinds_for_engine` + `migration_cost_delta` (kind-set diff,
  never "target cheaper").
- `src/forge_doctor_data/core/execution_model.py` — additive:
  `ExecutionScan.files_scanned` (exported file count) so COST007 only
  fires on real file evidence.
- `src/forge_doctor_data/knowledge/cost_drivers/` — **new** packs:
  `thresholds.json` (byte/count bounds), `drivers.json` (per-engine
  driver semantics, no pricing).
- `src/forge_doctor_data/cli/runtime.py` — `runtime cost` command
  (optional `--root` for entity drivers + transfer detection).
- `docs/checks.md` — COST### documented as runtime-scoped findings.
- `tests/unit/test_cost_drivers.py` — **new**, 15 tests.

## Design decisions

- `CREDIT_USAGE`/`RPU_USAGE`/`SLOT_USAGE` only emit when the
  corresponding unit is exported — duration→warehouse_uptime (Snowflake)
  and duration→rpu_usage (Redshift serverless) are labeled proxies.
- Cross-cloud transfer requires both clouds known — deployment-neutral
  engines (snowflake/trino/databricks/clickhouse) never guess a cloud.
- `CostPolicy.defaults()` reads the pack like `PerfPolicy`, inline
  fallback identical so the pack is a config surface.
- Migration delta compares driver *kinds* only; note explicitly
  disclaims relative cost.

## Validation

- `pytest tests/unit/test_cost_drivers.py` — 15 passed
- `pytest tests/ -x -q` — full suite
- `mypy` — clean, 257 files
- `ruff check` + `ruff format --check` — clean
