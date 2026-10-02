---
id: 182-cross-domain-rules
title: Cross-Domain Rule Engine — PLAT### findings over models+graph+capabilities
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_crossdomain.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_next_step.md` phase 1. Platform findings (PLAT###) derive
from multiple semantic models, canonical-graph edges, and capability
verdicts — never from a single domain.

# Acceptance Criteria
- `core/crossdomain.py`: `CrossDomainRule` (id, required entity kinds /
  relationships / capabilities, typed predicate), `ContributingFact`,
  `CrossDomainHit`, `RuleContext`. No DSL — typed Python only.
- `checks/platform_rules.py`: PLAT001 retrying orchestration +
  non-idempotent sink; PLAT002 runtime/config capability
  incompatibility; PLAT003 continuous writer + maintenance gap;
  PLAT004 duplicate orchestration ownership; PLAT005 IaC runtime vs
  source assumptions; PLAT006 table-format/consumer mismatch;
  PLAT007 stream sink side-effect idempotency.
- Findings list contributing facts (orchestration/compute/sink planes).
- `forge-doctor platform findings` CLI; same checks run under `scan`.
- Tests: true positive, prerequisite-absent FP, unknown capability,
  missing relationship, insertion-order determinism.
