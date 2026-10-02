---
id: 201
title: v1.0 Release Hardening
agent: claude
risk: low
verification:
  - python -m pytest tests/unit/test_api.py tests/unit/test_docs.py -x -q
  - python -m forge_doctor schema contracts scan-report
---

# Roadmap-2 Phase 10 - v1.0 Release Hardening

## Context

Before v1.0 can be tagged, the published contracts must actually match
the implementation, CI smoke must exercise them on all three OSes, and
the release decision needs a written pre-flight checklist.

## Acceptance Criteria

- `scan-report` JSON Schema in `core/schemas.py` describes the real
  `render_json` output (`tool`/`project` objects, `summary`, optional
  `suppressions`/`baseline`) — verified by a test that validates actual
  renderer output against the published schema.
- `golden-snapshot` schema describes the real snapshot files
  (row arrays + graph object) — verified against committed fixtures.
- `forge_doctor.api` exposes `SCAN_SCHEMA_VERSION` (scan contract,
  `"3.0"`) beside `SCHEMA_VERSION` (artifact family, `"1.0"`); docs and
  docstrings state the two-contract model accurately.
- CI smoke asserts the contract shape and exercises
  `schema contracts`, `explain`, and `checks` on the installed wheel
  (ubuntu/windows/macos).
- `docs/release.md` — v1.0 pre-flight checklist: gates, contract
  review, spec archival (human-only), version bump fields, tag +
  workflow steps.
- `docs/roadmap.md` gains the v0.8–v1.0 platform-intelligence summary.

## Constraints

- The `1.0.0` version bump and tag are **not** performed here — release
  is a maintainer decision. This phase hardens, it does not publish.
- `SCAN_SCHEMA_VERSION` stays `"3.0"` — the scan contract predates the
  SDK; its value is pinned by CI and must not silently change.

## Open questions

- Whether `SCHEMA_VERSION` (artifact family) should be unified with the
  scan contract at 1.0 — currently they version independently by design;
  unifying them is itself a breaking change.
