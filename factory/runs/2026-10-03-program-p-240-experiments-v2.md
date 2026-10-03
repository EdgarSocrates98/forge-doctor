# Run: spec 240 — Experiment / Simulation Intelligence 2.0

Program P, wave 6. Runtime-informed experiment comparison, deterministic
synthetic workloads, and extended Forge Lab ground truth.

## Scope

- `core/experiments_v2.py` — `ExperimentPlan` v2 (hypothesis / target /
  change / workload / baseline metrics / expected effects / protected
  constraints / measured metrics / acceptance), `MetricComparison`,
  verdict matrix:
  - `SUPPORTED` — all measurable expected effects confirmed, no
    protected-constraint violation
  - `NOT_SUPPORTED` — a measurable expected effect failed
  - `INCONCLUSIVE` — every expected effect unmeasurable (no evidence ->
    never invent), no violations
  - `CONSTRAINT_VIOLATED` — a protected constraint breached; overrides
    supported effects
- Deterministic seeded synthetic workload generators (`synthetic.py`
  behaviour folded into `experiments_v2`): uniform, skewed, burst,
  late-data, high-cardinality, many-small-files, hot-key, wide-row,
  deep-nesting, high-fanout — same seed -> identical artifact bundle.
- `core/lab.py` — additive ground-truth keys: `expected_signals`,
  `forbidden_signals`, `expected_root_causes` (already present),
  `expected_cost_drivers`, `expected_sla_status` (
  `<scope>.<metric>=met|violated|unverifiable`),
  `expected_optimization_candidates`; `_behavioral_categories` evaluates
  them against execution adapters + platform graph.
- `cli/lab.py` — `lab experiment` gains `--before/--after` bundle
  comparison with `--expect metric:op:value` and `--protect
  metric:op:value`; legacy `--hypothesis` flow unchanged.
- `analyzers/platform_graph_builder.py` — bridged already-extracted
  reliability attrs onto graph entities: Airflow task `retries` + DAG
  `default_retries`, streaming `checkpoint` (literal | `dynamic` |
  `none`), Lambda `dlq` (`true` | `none`).
- `core/reliability.py` — `checkpoint_dynamic` added to the
  checkpointing attr map so dynamically-expressed checkpoints read as
  DECLARED (not dropped).
- `core/performance.py` — wired `HIGH_SKEW`, `JOIN_AMPLIFICATION`, and
  `SMALL_FILE_AMPLIFICATION` into `extract_signals` (declared families
  that were previously never emitted; skew requires a real task-time
  distribution, join amplification needs join input/output evidence,
  small-files needs `files_scanned`).
- 11 new lab scenarios under `labs/performance/`, `labs/execution/`,
  `labs/cost/`, `labs/optimization/` exercising every execution adapter
  plus entity-attr cost drivers (opensearch replicas) and the
  negative-evidence case (iceberg-small-files: no file-count export ->
  `small_file_amplification` must NOT fire).
- `docs/checks.md` — Forge Lab section documents the new truth keys and
  the two `lab experiment` modes.

## Decisions

- No `labs/reliability/` fixture: SLA objectives (`sla_*`/`rpo`/`rto`
  entity attrs) have no analyzer emission path, so an
  `expected_sla_status` lab could not be populated through honest
  extraction. The machinery is covered by unit tests instead; the
  category activates the moment an objective source exists.
- Verdicts derive only from measured metrics + declared expectations —
  no workload execution, no invented numbers; unmeasured metrics are
  reported as `unmeasured`/`None`.
- `checkpoint="none"` / `dlq="none"` entity attrs encode *evidenced
  absence* (parser looked, found none) which the reliability extractor
  reads as `ABSENT`, distinct from missing attr (`UNKNOWN`).

## Benchmarks (parse + normalize, this machine)

| adapter   | n     | time   | per-exec |
|-----------|-------|--------|----------|
| spark     | 1,000 | 0.114s | 114µs    |
| snowflake | 1,000 | 0.070s | 70µs     |
| spark     | 10,000| 1.858s | 186µs    |
| snowflake | 10,000| 0.611s | 61µs     |

Roughly linear; the Spark eventlog cost is per-event JSON parsing.

## Validation

- `pytest tests/unit/ -k "experiment"` — 11+ passed (experiments v2 + v1)
- `pytest tests/unit/test_lab.py` — all pass; `lab run` — 60/60
  scenarios PASS including all 11 new ones
- `ruff check` / `ruff format --check` / `mypy` (260 files) — clean
- Full suite: **2004 passed** in 241.74s; `lab run` — 60/60 PASS

## Files

- `src/forge_doctor_data/core/experiments_v2.py` (new)
- `src/forge_doctor_data/core/lab.py`, `src/forge_doctor_data/cli/lab.py`
- `src/forge_doctor_data/core/performance.py`, `src/forge_doctor_data/core/reliability.py`
- `src/forge_doctor_data/analyzers/platform_graph_builder.py`
- `tests/unit/test_experiments_v2.py` (new)
- `labs/{performance,execution,cost,optimization}/**` (11 scenarios)
- `docs/checks.md`
