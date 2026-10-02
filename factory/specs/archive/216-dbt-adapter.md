---
id: 216
title: dbt adapter (transformation layer)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k dbt -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 2a - dbt

## Context

Wave 2 = transformation/semantic layer. dbt is the dominant artifact:
`dbt_project.yml`, `profiles.yml`, `schema.yml` (tests/docs),
`manifest.json` (observed compile output = richest evidence), model SQL
with `ref()`/`source()` edges. Feeds lineage into DataPlatformGraph.

## Acceptance Criteria

- `analyzers/dbt_model.py` — `DbtProjectModel`: project name/profile,
  model dirs, models (materialized: table|view|incremental|ephemeral),
  sources + freshness clauses, seeds, snapshots, tests (generic:
  unique/not_null/relationships/accepted_values; singular sql),
  exposures, macros used, schema.yml doc/test coverage per model.
- `manifest.json` (target/ or declared path) parsed as *observed*
  evidence: compiled graph, run results if `run_results.json` present.
- `ref()`/`source()` resolution → `READS_FROM`/`WRITES_TO` edges into
  DataPlatformGraph (`dbt_model`/`table`/`view` entities), linking to
  warehouse entities when adapters 213-215 detect the same platform.
- `DBT###` checks, minimum set: `DBT001` model without any test;
  `DBT002` incremental model without `unique_key`; `DBT003` source
  without freshness block; `DBT004` source declared but never `ref`ed;
  `DBT005` models without description/docs below threshold.
- CLI `dbt inspect .` (model summary, coverage, graph counts); lab
  suite `labs/dbt/*` with a real-ish project + adversarial non-dbt dir.

## Constraints

- No `dbt compile`/`dbt run` — read existing artifacts only.
- profiles.yml may contain env-var secrets patterns; do not surface
  secret values in findings (only key names).

## Open Questions

- Semantic-layer subset (metrics/semantic models in dbt >=1.6) — in
  scope here or deferred to a dedicated semantic-layer spec? Default:
  parse + surface, no dedicated checks yet.
