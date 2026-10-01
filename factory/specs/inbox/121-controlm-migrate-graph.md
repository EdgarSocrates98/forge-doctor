---
id: 121-controlm-migrate-graph
title: Control-M stage 6 — migration advisor + project-graph integration
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Final stage of `prompt_evo_control-m.md`. Two pieces:
1. `forge-doctor controlm migrate --from X --to Y` crossing jobs/plugins/
   API usage against `knowledge/controlm/compatibility.json`.
2. Project-graph integration — link Control-M jobs to the artifacts they
   trigger (`ctm` run commands referencing scripts → job files → Glue/Spark/
   Iceberg tables), so findings gain orchestration context
   ("this collect() runs inside a critical Control-M chain").

# Acceptance Criteria
- `controlm migrate` mirrors `glue migrate`'s shape; conservative
  compatibility pack (documented floors, HIGH when unknown).
- Graph integration: `CtmJob` ↔ project files via command/script references
  (exact filename matching only — no fuzzy linking); surface in `inspect`
  and (if cheap) annotate related findings.
- knowledge/controlm/compatibility.json + versions.json with sources.

# Constraints
- Graph links only on unambiguous filename matches; no execution.
