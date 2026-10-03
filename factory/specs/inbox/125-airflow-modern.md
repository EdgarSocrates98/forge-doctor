---
id: 125-airflow-modern
title: Airflow stage 4 — modern Airflow — assets, event-driven, dynamic mapping, deferrable
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 4 of `prompt_evo_airflow.md`. Airflow 3 assets (datasets renamed),
event-driven scheduling (`AssetWatcher`), dynamic task mapping
(`expand()`), deferrable operators depth.

# Acceptance Criteria
- `AirflowAssetGraph`: `Asset(uri)`/`Dataset(uri)` outlets/inlets on tasks
  and DAG-level `schedule=asset` consumers — produced-never-consumed
  (AIR070), consumed-no-producer (AIR071), duplicate URI (AIR072),
  noncanonical URI (AIR073), schedule+asset conflict (AIR074),
  asset cycle (AIR075).
- Dynamic mapping: unbounded `expand()` (AIR052), static-loop task gen
  that's a clean mapping candidate (AIR051 — only when the loop source is a
  literal list).
- Sensors: polling candidate for event-driven (AIR080 — s3/file sensor with
  tight poke_interval), synchronous sensors count (AIR044).
- `forge-doctor-data airflow assets <path>` — asset graph + unmatched.

# Constraints
- Dataset→Asset rename handled (both recognized; Asset canonical).
