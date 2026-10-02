---
id: 222
title: Data quality evidence (Deequ, Great Expectations, Soda, dbt tests)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "quality or deequ or expectations or soda" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 5 - Data quality intelligence

## Context

Doc wave 5 = data quality. Expectation suites are *evidence of intent*:
Deequ `Analyzer`/`Check` code, Great Expectations `expectations/*.json`
suites + checkpoints, SodaCL `checks.yml`, dbt tests (via spec 216).
The valuable cross-domain finding: **prod tables with no tests** and
**expectations never wired to a gate** (defined but never run).

## Acceptance Criteria

- `analyzers/quality_model.py` — `DataQualityModel`: expectation suites
  per engine (deequ|great_expectations|soda|dbt), coverage map
  table→checks, gate/checkpoint definitions, observed results
  (`run_results`/`validations` exports).
- `DQ###` checks: `DQ001` observed/table entity marked prod (name/env
  signal) with zero expectations; `DQ002` suite defined but no
  checkpoint/pipeline reference (defined-not-run); `DQ003` suite covers
  a table not in the graph (stale suite); `DQ004` expectation on a
  column dropped in the detected schema (contract/quality drift).
- `quality inspect .` CLI showing coverage table (table × checks) +
  coverage ratio.
- Lab suites per engine + adversarial (random `checks.yml` for CI tools
  must not fire DQ rules without quality-engine markers).

## Constraints

- Parse suites; never execute GX/Deequ code. Results arrive only as
  exported artifacts (observed evidence kind).

## Open Questions

- Coverage threshold for DQ001 — default: flag when a prod-signaled
  table has zero checks AND the project has ≥1 suite (i.e., quality
  practice exists but gap). Tune at grill.
