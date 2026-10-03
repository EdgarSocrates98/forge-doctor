---
id: 242
title: Performance Regression Intelligence — baseline-aware regression detection
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "regression or perfreg" -x -q
  - python -m pytest tests/ -x -q
  - ruff check src tests && ruff format --check src tests && mypy
---

# Program Q — Phase 2: Performance Regression Intelligence (prompt_evo_step9 §PHASE 2, §78–80, §91)

Detect when behavior worsens vs baseline. Extends — does not duplicate —
the phase-236 `FingerprintBaseline`/`classify_regression` surface.

## Acceptance Criteria

- `RegressionSignal` + classes: NEW / IMPROVED / REGRESSED / STABLE /
  VOLATILE / INSUFFICIENT_DATA.
- Dimensions: duration, queue, scan, shuffle, spill, memory, CPU,
  freshness, error_rate, throughput.
- Deterministic detection: current > historical p95, or
  current-median > baseline-median * configured factor, or MAD-based
  deviation. No global magic threshold; threshold priority: explicit
  contract > named baseline > historical baseline > knowledge-pack.
- `RegressionConfidence` HIGH/MEDIUM/LOW/UNKNOWN from sample count,
  baseline stability, metric completeness, evidence quality.
- Findings PERFREG001–PERFREG009 (duration/queue/scan/shuffle/spill/
  memory/freshness/throughput regression, volatility increase).
- `RegressionEpisode` (start, end, subject, dimensions, baseline,
  current, confidence, related_changes).
- One isolated run = *candidate* regression, not persistent.
  Persistence classes: ONE_OFF / BURST / PERSISTENT / RECOVERED /
  FLAPPING (bad-good oscillation detection; RECOVERED when runtime
  returns to baseline).
- No seasonality forecasting; allow hour/day-segmented baselines later.
- `runtime regressions` CLI; sample output per prompt §91.

## Constraints

- correlation != causation; trend != root cause.
- Explain observed/derived/threshold/evidence on every finding.
