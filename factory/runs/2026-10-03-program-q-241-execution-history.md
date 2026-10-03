# Run: spec 241 — Execution History & Baselines

Program Q, wave 1. Temporal series per subject over normalized
`QueryExecution` rows — the foundation every later wave consumes.

## Scope

- `core/trends.py` (new) — reusable `TrendSeries` + `SeriesPoint` +
  `TrendDirection` and deterministic robust stats (`median`,
  nearest-rank `percentile`, `mad`, `trend_direction`,
  `metric_baseline`). Cross-phase primitive for performance / cost /
  capacity / freshness / reliability (§8).
- `core/execution_history.py` (new) —
  - `ExecutionSample`: compact metric observation (timestamp,
    timestamp_quality, execution_id, fingerprint, engine, duration,
    queue, bytes/rows in+out, shuffle, spill, cpu, memory, network,
    freshness, status, evidence). Fingerprint only — never SQL text.
  - `TimestampQuality`: UTC_ALIGNED / EPOCH_ASSUMED_UTC /
    LOCAL_UNALIGNED / UNKNOWN (§9-10, §81).
  - `ExecutionSeries`: `{kind}:{subject}` ids, deterministic order
    (timestamp, execution_id), `first_seen`/`last_seen`,
    `metric_values` (distribution stats — no timestamps needed) and
    `metric_series` (trend — timestamps required).
  - `BaselineWindow` (last_n / last_days / named / release) +
    `ExecutionBaseline`: per-metric median/p50/p90/p95/p99/min/max/MAD/
    trend + evidence-based confidence ladder.
  - Snapshot storage: versioned JSONL under
    `.forge-doctor-data/execution-history/`, `forge-doctor-data/execution-
    history@1` + tool_version; `record_executions` streams writes,
    `read_snapshot`/`iter_samples` stream reads (constant memory).
  - `HistoryRetention` (keep_days / keep_samples / compact_after) +
    `prune_history` + `compact_history` (daily aggregate medians —
    baselines survive, raw detail drops). Config parsed from
    `[tool.forge-doctor-data.history]`.
  - `observed_fact_series` — OBSERVED twin facts join execution series
    on explicit entity identity (§1.8).
  - Experiment isolation: `kind="experiment"` writes `exp-*.jsonl`;
    production readers never see them (§22).
- `cli/runtime.py` — `runtime history` (record artifact batch or list
  series), `runtime baseline` (`--last N` / `--days N` / `--metric`),
  `runtime trend` (`--metric`). Unmatched/unreadable artifacts exit 2
  without recording an empty snapshot.
- `core/config.py` — additive `history_retention` parsing.

## Decisions

- Distribution statistics intentionally accept undated samples
  (`metric_values`); only trend ordering requires timestamps — a median
  is evidence without a clock.
- `build_series` falls back to `query_id`/`execution_id` for
  JOB/PIPELINE/WORKLOAD subjects when no workload identity exists —
  series still honest, never skipped silently.
- No new storage dependency: JSONL only, per §58.

## Benchmarks (this machine)

| n samples | record | stream-read all |
|-----------|--------|-----------------|
| 10k       | 0.38s  | 0.31s (~38µs/sample) |
| 100k      | 4.12s  | 4.02s           |
| 1M        | 29.5s  | 30.5s           |

Series rebuild + baselines over 1.11M stored samples / 500 series:
build 97.8s, baselines 18.0s — linear, streaming (O(1) memory).

## Validation

- `pytest tests/unit/test_execution_history.py` — 23 passed
- `pytest -k "history or baseline or series or trend"` — 43 passed
- `ruff check` / `ruff format --check` / `mypy` (262 files) — clean
- CLI smoke: record → baseline (median=1800ms p95=2400ms over 2
  samples, confidence=unknown) → trend (insufficient_data, no
  timestamps — honest)
- Full suite: **2027 passed** in 301.74s

## Files

- `src/forge_doctor_data/core/trends.py` (new)
- `src/forge_doctor_data/core/execution_history.py` (new)
- `src/forge_doctor_data/core/config.py`
- `src/forge_doctor_data/cli/runtime.py`
- `tests/unit/test_execution_history.py` (new)
