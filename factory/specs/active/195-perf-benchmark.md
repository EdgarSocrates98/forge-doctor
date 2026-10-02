---
id: 195
title: Performance & Scale Benchmark
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_bench.py tests/unit/adversarial/test_bench.py -x -q
  - python -m forge_doctor bench run --files 200
  - python -m forge_doctor bench run --files 200 --budget <file>
---

# Roadmap-2 Phase 4 - Performance & Scale Benchmark

## Context

Adoption needs evidence the engine scales. This phase adds a
deterministic synthetic corpus generator plus cold/warm scan, AST parse
count, graph build, pack load, and memory measurements — with
machine-portable budgets.

## Acceptance Criteria

- `generate_project(root, files, seed)` — deterministic (same seed →
  byte-identical corpus), mixed .py/.tf/.sql/.txt.
- `run_bench(path, budget)` → `BenchResult`: cold_ms, warm_ms (disk
  cache warm), ast_parsed, graph_ms + entity/edge counts, pack_ms +
  pack count, peak_mb (tracemalloc), findings.
- Budgets: `warm_ratio_max`, `ast_parse_max_ratio`,
  `graph_ms_per_1k_files`, `cold_ms_per_1k_files` — violations become
  `budget_failures` and a non-zero exit, never crashes on missing
  denominators.
- `forge-doctor bench run [path|--files N --seed S] [--budget f.json]
  [--json]`; synthetic corpora go to a temp dir, never the project.
- Tests: determinism, seed sensitivity, one-parse-per-file, budget
  pass/fail paths, zero-denominator honesty.

## Constraints

- Budgets are ratios/counts, not wall-clock absolutes — machines differ.
- tracemalloc bounded to the cold scan only.
- No wall-time assertions in tests that could flake on slow CI.

## Review Notes

- Reference point (dev machine, 200 files): cold 13.1s, warm 5.5s
  (42%), 122/122 parses, graph 1.3s (117 ent/63 rel), 74 packs 34ms,
  peak 12MB.
