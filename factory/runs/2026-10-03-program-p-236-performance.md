# Run: Program P wave 2 — Cross-engine performance intelligence (spec 236)

- **Commit**: this commit
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/236-cross-engine-performance.md`

## Scope

Performance layer over the spec-235 execution model: normalized signal
families, evidence-aware thresholds, baselines/trends/regression
classification, physical-design extraction, and PHY findings.

## Files changed

- `src/forge_doctor_data/core/performance.py` — **new**: `SignalFamily`
  (12 cross-engine families), `PerformanceSignal`,
  `SkewSignal`/`skew_signals` (real distributions only, n>=4),
  `QueuePressure`, `PerfPolicy` + `perf_findings` (PERF001–PERF010),
  `ExecutionTrend`/`build_trends`, `FingerprintBaseline`/`baseline`,
  `classify_regression` (NEW/IMPROVED/REGRESSED/STABLE/
  INSUFFICIENT_DATA, relative vs p95), `DataGravitySignal`/
  `data_gravity` (facts only, no score).
- `src/forge_doctor_data/core/physical_design.py` — **new**:
  `PhysicalDesign` (partitioning, clustering, ordering, distribution,
  sharding, indexing, replication, caching) extracted from
  evidence-tagged graph entity attrs; `physical_findings` PHY001–005.
- `src/forge_doctor_data/knowledge/performance/` — **new** packs:
  `thresholds.json` (policy defaults), `engines.json` (per-engine
  metric semantics/limitations), loaded via `knowledge.load_pack`.
- `src/forge_doctor_data/cli/runtime.py` — `runtime performance` command.
- `docs/checks.md` — PERF###/PHY### documented as runtime-scoped
  findings beside MIGR/SQLPORT.
- `tests/unit/test_performance.py` — **new**, 18 tests.

## Design decisions

- Amplification ratios emit only when denominators are measured;
  proxies (`partitions scanned/total`, `rows returned/scanned`) keep
  explicit basis labels.
- Skew requires real task distributions — never inferred from code.
- Spill/queue families normalize shape, not metric equivalence across
  engines (documented in `engines.json`).
- PERF/PHY are runtime-scoped findings (not registry checks) — emitted
  by `runtime performance`, keeping `scan` hermetic.
- PHY001 (static, no org keys) and PHY004 (storage-shape signals)
  added to complete the spec'd 001–005 range.

## Validation

- `pytest tests/ -x -q` — 1949 passed
- `mypy` — clean, 256 files
- `ruff check` + `ruff format --check` — clean
