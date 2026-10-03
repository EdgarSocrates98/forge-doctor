---
id: 204
title: Fleet / Estate Intelligence
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k fleet -x -q
---

# Roadmap-3 Phase 3 - Fleet / Estate Intelligence

## Context

Workspace covers a handful of nested repos. Estate intelligence needs to
answer org-scale questions across a manifest of repositories: "which
jobs still run Glue 4", "which Iceberg v1 tables block migration",
"retry+append without idempotency", "runtimes outside policy".

## Acceptance Criteria

- `forge-doctor-data fleet <manifest>` — manifest is a YAML/JSON file listing
  repo paths (or a parent dir scanned via workspace discovery); builds
  the merged estate platform graph (reuses `WorkspaceModel` scaling).
- `forge-doctor-data fleet query` — deterministic estate queries over the
  merged graph + models, e.g.:
  - `runtimes` — entity counts by platform/version (glue_version, dbr…)
  - `capability <id>` — which entities satisfy/violate a capability
  - `dependents <entity|glob>` — everything depending on an asset
  - `findings <check-id>` — repos/entities producing a check id
- `fleet report` — estate census: repos, entities by kind/domain,
  findings by category, policy violations; text + JSON.
- Output is deterministic and documented in docs/checks.md or a
  `docs/fleet.md`; scale target documented honestly (tested at fixture
  scale ~10 repos; linear cost noted).

## Constraints

- Read-only; offline; reuses ProjectContext/WorkspaceModel — no
  duplicate graph merge logic.
- Hundreds of repos is the aspiration, not the test floor; benchmark at
  fixture scale and record limits in the run record.

## Open questions

- Remote repo fetching (clone org repos automatically)? Deferred —
  manifest lists local paths only.
