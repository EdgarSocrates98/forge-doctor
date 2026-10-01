---
id: 118-controlm-governance
title: Control-M stage 3 — governance — site standards, naming, agents, profiles
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 3 of `prompt_evo_control-m.md`. Governance checks over 116's model:
site standards as data (rules in definitions), naming conventions,
Application/SubApplication consistency, agent/host-group references,
connection-profile misuse.

# Acceptance Criteria
- Evaluate `SiteStandard` objects' required-field rules against jobs
  (CTM050/CTM051/CTM052); naming-convention regex if standard defines one
  (CTM053); inconsistent Application/SubApplication pairs (CTM054).
- Agent/host-group reference checks (CTM060-064): referenced but undeclared
  host group, single-host where group expected, inconsistent agent choice
  across sibling jobs.
- Connection profiles: missing reference, prod profile referenced from
  non-prod definition, embedded token (CTM072-074, complements CTM070).
- All severities/messages follow existing conventions; deterministic.

# Constraints
- Site-standard rule format: accept both inline rules and the repo's own
  `site_standards.json` knowledge pack defaults when no standard object
  exists. Product decision parked if the format proves ambiguous.
