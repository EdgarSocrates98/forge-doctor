---
id: 120-controlm-sla-diagnose
title: Control-M stage 5 — SLA doctor + error knowledge pack for `diagnose`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 5 of `prompt_evo_control-m.md`. SLA Management fields on jobs/folders
(`Service`/`SLA`-related props) + critical-path reasoning over 117's graph.
Plus `knowledge/errors/controlm/` families consumed by `diagnose`.

# Acceptance Criteria
- SLA checks: CTM040 endpoint disconnected from chain, CTM042 critical
  service without SLA definition, CTM043 schedule makes SLA mathematically
  risky (window × dependency depth), CTM045 long chain without SLA.
- `forge-doctor controlm sla <path>` — per-service critical path + risks
  (reviewer's example shape).
- Expand `knowledge/errors/controlm.json` into `knowledge/errors/controlm/`
  per-family packs (agent ping, OSCOMPSTAT, deploy validation, SLA delay,
  calendar issue, auth failure) wired into `diagnose`.

# Constraints
- SLA math must be conservative (only flag when statically provable).
