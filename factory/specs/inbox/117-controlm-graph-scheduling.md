---
id: 117-controlm-graph-scheduling
title: Control-M stage 2 — dependency graph + scheduling engine, `controlm schedule`
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_controlm_model.py tests/unit/checks/test_controlm.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Stage 2 of `prompt_evo_control-m.md`, on top of spec 116's `ControlMModel`.
Build the job dependency graph (wait/add events + explicit in/out
conditions) and effective scheduling.

# Acceptance Criteria
- Graph over `CtmJob`s: edge producer→consumer per shared event name;
  missing-producer events flagged (deepens CTM009).
- New checks: unreachable job, circular dependency (CTM005/CTM006),
  ambiguous event producer (CTM008 — >1 producer for a consumed event),
  schedule-never-fires heuristics (CTM029: impossible WeekDays+Calendar
  intersection, conflicting FromTime/ToTime CTM027, cyclic without end
  CTM026, narrow window CTM025 when downstream of a longer-window upstream).
- `forge-doctor-data controlm schedule <path>` — per-folder effective schedule
  view (calendar, window) + risks, matching the reviewer's example shape.
- `forge-doctor-data controlm graph <path>` — text/dot rendering of the DAG.

# Constraints
- No SCC/heavy graph lib — small deterministic topo/cycle impl.
- Findings keep deterministic ordering (folder, job name).
