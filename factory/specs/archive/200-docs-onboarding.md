---
id: 200
title: Documentation + Onboarding Completeness
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_docs.py -x -q
---

# Roadmap-2 Phase 9 - Documentation + Onboarding Completeness

## Context

The engine shipped ~20 new command groups across the ten phases and
Roadmap-2; the README still described the original check-runner surface.
New users could not discover `lab`, `golden`, `bench`, `policy`,
`what-if`, the domain `inspect` groups, or the platform-graph tooling
without reading source. The README JSON example also showed a stale
`schema_version`.

## Acceptance Criteria

- README usage block covers every top-level command and command group;
  the checks table covers every registered check category.
- Stale facts fixed (scan JSON `schema_version` is `1.0`).
- `docs/getting-started.md` — install → first scan → explain/trace →
  governed suppressions → CI baseline gating → formats → next steps.
- CONTRIBUTING documents the new contributor surfaces: knowledge packs,
  lab scenarios, golden repos, policy packs.
- `tests/unit/test_docs.py` enforces completeness deterministically:
  every CLI command/group name in README, every check id and category
  in `docs/checks.md`, all relative doc links resolve, `api.__all__`
  names exist.

## Constraints

- Docs only — no product behavior changes.
- Links and command names must be real (verified against the Typer app).
