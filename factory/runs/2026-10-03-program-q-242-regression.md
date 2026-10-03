# Run: spec 242 — Performance Regression Intelligence

Program Q, wave 2. Baseline-aware regression detection over the
spec-241 history surface.

## Scope

- `core/regression.py` (new) —
  - `RegressionDimension` (10 axes incl. derived `error_rate` and
    `throughput` = rows/sec, direction-aware: throughput is
    lower-is-better)
  - `RegressionSignal` with deterministic rules: current > historical
    p95, current-median > baseline-median × factor, or MAD deviation;
    VOLATILE class for unstable baselines (MAD/median ratio)
  - `RegressionConfidence` HIGH/MEDIUM/LOW/UNKNOWN from evidence
    quantity
  - `RegressionEpisode` + `classify_persistence` — ONE_OFF / BURST /
    PERSISTENT (3+ trailing breaches) / RECOVERED / FLAPPING
  - `RegressionPolicy.defaults()` loads
    `knowledge/performance/regression.json`; falls back inline so the
    pack stays a config surface
  - `perfreg_findings` → PERFREG001–009 on the `PerformanceFinding`
    carrier; WARNING only for PERSISTENT, INFO for candidates
- `core/performance.py` — `RegressionClass.VOLATILE` added (additive).
- `cli/runtime.py` — `runtime regressions` (artifact or recorded
  history; per-signal class + basis + confidence).
- `knowledge/performance/regression.json` — declared thresholds with
  rationales and sources.
- `docs/checks.md` — PERFREG section added.

## Decisions

- A single slow run can only ever produce an INFO *candidate* —
  persistence requires consecutive breaches (§2.8 honored).
- Baseline = all-but-latest samples; current = last-3 median, so one
  outlier at the tail cannot trigger a breach by itself.
- Derived dimensions stay honest: `error_rate` needs status strings,
  `throughput` needs rows + duration — absent inputs keep the signal
  INSUFFICIENT_DATA and it is filtered out, not faked.

## Validation

- `pytest tests/unit/test_regression.py` — 18 passed
- `ruff check` / `ruff format --check` / `mypy` (263 files) — clean
- Full suite: 2045 passed

## Files

- `src/forge_doctor_data/core/regression.py` (new)
- `src/forge_doctor_data/core/performance.py` (VOLATILE member)
- `src/forge_doctor_data/cli/runtime.py`
- `src/forge_doctor_data/knowledge/performance/regression.json` (new)
- `tests/unit/test_regression.py` (new)
- `docs/checks.md`
