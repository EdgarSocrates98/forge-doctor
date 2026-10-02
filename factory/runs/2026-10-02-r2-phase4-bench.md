# Roadmap-2 Phase 4 run — Performance & Scale Benchmark

- Spec: `factory/specs/active/195-perf-benchmark.md`
- Commit message: `feat(bench): add performance and scale benchmark`

## Implemented

- `core/bench.py`:
  - `generate_project(root, files, seed)` — deterministic mixed
    .py/.tf/.sql/.txt corpus (55/15/15/15 split), same seed → identical
    bytes.
  - `run_bench(path, budget)` → `BenchResult`: cold/warm scan ms,
    ast_parsed, graph_ms + entity/edge counts, pack_ms + pack count,
    peak_mb (tracemalloc), findings count.
  - Budgets: `warm_ratio_max`, `ast_parse_max_ratio`,
    `graph_ms_per_1k_files`, `cold_ms_per_1k_files` — portable
    ratios/counts, violations → `budget_failures`, None-safe.
- `cli/bench.py` — `bench run [path | --files N --seed S]
  [--budget f.json] [--json]`; synthetic corpora land in tempdir.

## Reference numbers (dev box, 200-file corpus, seed 7)

    py=122 tf=25 sql=29 other=24 · 103 findings
    cold 13.05s · warm 5.54s (0.42) · 122/122 AST parses
    graph 1.32s (117 entities / 63 edges) · 74 packs in 34ms · 12MB peak

## Verification

- `pytest tests/unit/test_bench.py tests/unit/adversarial/test_bench.py`
  → 12 passed (determinism, seed sensitivity, budget pass/fail,
  zero-denominator honesty, malformed budget)
- `bench run --files 200` + `--json` verified
- Full suite: 1245 passed; mypy 148 files clean; ruff/format clean.

## Open questions

- A `warm_ratio_max` budget isn't bundled yet — absolute thresholds are
  machine-dependent; CI can pin `ast_parse_max_ratio=1.0` +
  `graph_ms_per_1k_files` safely.
