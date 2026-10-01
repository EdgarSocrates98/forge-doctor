---
id: 127-airflow-migrate
title: Airflow stage 6 — migration 2→3 + providers, `airflow migrate|providers`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 6 of `prompt_evo_airflow.md`. Airflow 2→3 migration advisor (SDK,
Assets rename, auth manager, deferrable defaults) + provider compatibility.

# Acceptance Criteria
- `knowledge/airflow/{versions,migration,providers,deprecations}.json`
  (schema_version 2 + sources, conservative floors).
- `forge-doctor airflow migrate --from 2.x --to 3.x` — mirrors
  `glue migrate` shape: deprecated import paths (AIR133), Dataset→Asset
  API changes, removed config keys, provider floors.
- `forge-doctor airflow providers` — imported vs declared provider map,
  version-compat hints (AIR131 unused provider dep complements 122's
  AIR130).

# Constraints
- Pack-driven; unknown versions → HIGH "no bundled data", never guess.
