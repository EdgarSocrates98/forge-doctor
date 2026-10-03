---
id: 241
title: Execution History & Baselines — temporal series per subject
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "history or baseline or series" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 1: Execution History & Baselines (prompt_evo_step9 §PHASE 1, §8–9, §40–43, §56–59, §81)

From "I understand this execution" to "I understand how this platform
behaves over time". Builds temporal series per subject (fingerprint /
job / pipeline / dataset / workload / engine) consuming `QueryExecution`
— never duplicating parsing.

## Acceptance Criteria

- `ExecutionSeries` (series_id, subject_kind, subject_id, engine,
  fingerprint, samples, first_seen, last_seen, evidence_sources)
- `ExecutionSample` (timestamp, execution_id, duration, queue_time,
  bytes_read/written, rows_read/written, shuffle, spill, cpu, memory,
  network, freshness, status, evidence)
- `ExecutionHistorySnapshot` — compact normalized storage,
  `schema_version` + `tool_version` + knowledge versions; JSONL/compact
  JSON only, no new DB dependency; streaming readers (never load-all).
- `ExecutionBaseline` per fingerprint/job/pipeline/dataset: sample_count,
  window (last-N-execs / last-N-days / named / release), median, p50/p90/
  p95/p99, min, max, MAD, trend, confidence — robust stats, no magic
  thresholds.
- `TrendSeries` — reusable trend primitive shared by performance / cost /
  capacity / freshness / reliability (cross-phase §8).
- Time model: timestamps normalized to UTC, source tz metadata
  preserved; `TimestampQuality` for cross-system clock alignment.
- Retention config (`history.keep_days`, `history.keep_samples`,
  `history.compact_after`) and compaction (samples → daily aggregates →
  long-term summaries) without losing baselines.
- History sanitization: never persist secrets / raw credentials /
  sensitive SQL literals (reuse existing redaction).
- Deterministic total ordering of series/samples; obs runtime facts get
  historical series via twin integration.
- CLI: `runtime history`, `runtime baseline`, `runtime trend`.
- Experiments v2 can write isolated `ExecutionSeries` (not mixed with
  production history).

## Constraints

- Deterministic, offline, no cloud calls, no target execution, no LLM.
- Percentile/MAD calculations must be deterministic and tested.
- Portable public API preserved; expose only mature objects.

## Verification

- Unit tests: empty history, single sample, multiple samples, outlier,
  stable series, slow trend, fast regression, missing metrics,
  deterministic percentiles, snapshot roundtrip.
- Benchmarks for 10k/100k/1M compact summaries recorded in run record.
