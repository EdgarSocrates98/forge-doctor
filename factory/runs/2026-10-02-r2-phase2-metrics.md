# Roadmap-2 Phase 2 run — Quality Metrics

- Spec: `factory/specs/active/193-lab-metrics.md`
- Commit message: `feat(metrics): add Forge Lab precision recall and coverage metrics`

## Implemented

- `core/lab.py`:
  - `GroundTruth.allowed_findings` — scenario-level known-benign ids;
  - `labs/_defaults.json` — lab-level allowlist merged into every
    scenario's truth (`_merge_defaults`);
  - `run_scenario` now records `stats`: `fp_candidates`, `extra_info`,
    `allowed_hits`, `py_files`, `py_parsed`, `graph_edges`,
    `runtime_models`; `CategoryResult.extra` only lists genuinely
    unreviewed detections.
- `core/metrics.py` — `MetricRow` (per-domain + TOTAL) with
  `precision`, `recall`, `fpr` (vs forbidden declarations),
  `parser_coverage`, `graph_recall`, `capability_accuracy`,
  `root_cause_recall`; `None` when denominator is 0.
  `compute_metrics` + `forbidden_declarations` helpers.
- `cli/lab.py` — `lab metrics [--labs] [--json]` printing the
  doc-format table (expected/detected/missed/fp + rates).

## Semantics

- FP candidates = WARNING/ERROR detections neither expected nor allowed
  (INFO anchors describe surface, never FPs).
- Precision vs non-exhaustive truth would understate reality — the
  allowlist is the mechanism for declaring reviewed noise.
- Current suite: 24 expected, 24 detected, 0 missed, 0 FPs, 5 forbidden
  declarations all clean → precision/recall/graph/capability/root-cause
  = 100%, FPR = 0%, parser coverage 100%.

## Verification

- `pytest tests/unit/test_lab.py tests/unit/adversarial/test_lab.py` →
  25 passed (4 new metrics tests incl. forbidden-hit FPR, defaults
  merging, zero-denominator)
- `forge_doctor lab metrics` + `--json` verified
- Full suite: 1221 passed; mypy 144 files clean; ruff/format clean.
