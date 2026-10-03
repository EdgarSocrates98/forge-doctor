---
id: 132-platform-capability
title: Cross-domain — DataPlatformModel, capability engine, `platform inspect`
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
The capstone of `prompt_evo_databricks_emr.md`: a `platform inspect`
command + capability matrix so checks query
`capabilities.evaluate("ICEBERG_MERGE_WRITE", env)` instead of embedding
per-runtime `if`s. Depends on 113 (done), 114, 129, 130, 131.

# Acceptance Criteria
- `DataPlatformModel`: composes controlm/airflow/iceberg/spark/glue models
  into one orchestration→compute→storage→governance summary; every leg
  evidence-gated (absent model = "not detected", never inferred).
- Capability engine: `knowledge/platform/capabilities.json` declares
  requirements (runtime≥X, format-version, access model); a checker
  evaluates against fused evidence; unknown inputs → UNKNOWN, not denied.
- `forge-doctor-data platform inspect` — the reviewer's output shape
  (Orchestration/Compute/Engine/Storage/Catalog/Governance/Operations/
  Findings).
- At least one cross-domain check exercised via the engine
  (ICEBERG_MERGE_WRITE × Glue runtime × catalog).

# Constraints
- Capability results must render evidence, not verdicts alone.
