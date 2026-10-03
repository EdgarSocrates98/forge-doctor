# Program Q — Wave 3: Change → Runtime Correlation (spec 243)

## Scope

Connect semantic/architecture/config/deployment change with runtime
behavior change, reusing `change_intel` + `semantic_diff` evidence —
no parallel taxonomy.

## What landed

- `core/change_correlation.py`
  - `ChangeClass` — 11 classes (runtime_upgrade, schema, partition,
    distribution, config, security, orchestration, capacity, query,
    materialization, dependency).
  - `classify_change` — attr-key fragments → class, specificity-ordered,
    fallback CONFIG_CHANGE.
  - `ChangeEvent` — id/timestamp/source/entities/properties/class/commit/
    evidence.
  - `change_events_from_diff` — events only from entity-mapped
    `EntityChange`s; `touched` and unmapped files emit nothing, so a
    docs-only commit has zero correlation surface.
  - `change_events_from_json` — deployment-export shape (CI/TF/dbt);
    rows without entities skipped, malformed file → empty.
  - `ChangeRuntimeCorrelation` — `matching_dimensions`, temporal/
    graph distances, shared entities, per-leg breakdown, confidence,
    explanation.
  - `correlate` — emission requires **locality** (entity overlap or
    bounded ≤3-hop undirected graph path) **and** at least one relevant
    matching dimension, then ≥2 total legs. Confidence: 4 legs HIGH,
    3 MEDIUM, else LOW. Temporal leg needs real timestamps on both
    sides (`TimestampQuality` honest — no clock alignment, no temporal
    claim).
  - `PlanFingerprint`/`plan_changes` (§13-16) — structural stage-kind +
    join-strategy fingerprint; detects JOIN_STRATEGY_CHANGED,
    SCAN_PATH_CHANGED, EXCHANGE_ADDED/REMOVED, SCAN/PARALLELISM/
    MATERIALIZATION classes.
  - `DataShapeSnapshot` + `shape_delta` — rows/bytes/files/partitions/
    cardinality/skew ratios so data growth is not blamed on code.
- `cli/runtime.py` — `runtime correlate <events.json>` (+ `--root`,
  `--window` minutes, `--format json`).
- `cli/diff.py` — `--runtime-impact` flag on the existing `diff`
  command (implies `--semantic`); change events are extracted from the
  semantic diff, stamped with the head commit's git timestamp, then
  correlated against recorded history. JSON output gains a
  `runtime_impact` block.
  - Deviation: spec text says `diff runtime-impact`. `diff` is a
    single command (not a group) and converting it would break the
    positional `diff <old> <new>` contract; the flag preserves the CLI
    surface while delivering the capability.
- `core/lab.py` — truth keys `expected_regressions`
  (`<subject>.<dim>=<class>`, `*` wildcard), `expected_correlations`
  (`<change_id>[=confidence]`), `forbidden_correlations`; evaluated
  over `runtime/` artifacts + `changes.json` in the scenario root.
- Labs: `labs/regression/partition-change` (regressed duration +
  MEDIUM correlation) and `labs/regression/docs-only`
  (forbidden_correlation — no locality ⇒ nothing emitted).
- `docs/checks.md` — Change ↔ Runtime Correlation section.

## Language discipline

Output uses `correlated with` + an explicit per-leg breakdown. No
`caused`/`root cause` wording; `confirmed cause` is reserved for
spec-244 deterministic chains.

## Validation

- `pytest tests/unit/ -k "correlat or change" -x -q` — 54 passed
- `tests/unit/test_change_correlation.py` — 18 tests (classification,
  diff→events incl. touched/docs-only suppression, JSON events,
  correlation gating, plan fingerprints, shape deltas)
- lab run on `labs/regression` — 2/2 PASS
- `ruff check src tests`, `ruff format --check`, `mypy src` — clean
  (234 files)
- Full suite: **2064 passed** in 177.67s
