# Run — R2 Phase 10: v1.0 Release Hardening

Spec: `factory/specs/active/201-v1-release-hardening.md`
Date: 2026-10-02 · Agent: claude · Result: built, awaiting review

## Delivered

- **Contract defect found and fixed** — the Phase-8 published schemas
  contradicted real output:
  - `scan-report` declared `tool`/`project` as strings; the renderer
    emits `tool: {name, version}` and `project: {name, root?}` objects.
    Also: scan reports are versioned `"3.0"` (`JSON_SCHEMA_VERSION`),
    not the artifact-family `SCHEMA_VERSION` `"1.0"`.
  - `golden-snapshot` declared a single object with a `tool` key; real
    snapshots are sorted row arrays (findings/migrations/remediations/
    root_causes) or a bare `{entities, relationships}` graph object.
  - Both schemas rewritten to match; `README` example restored to
    `"3.0"` (the Phase-9 "fix" to `"1.0"` was itself a regression —
    caught by the CI smoke assert that already pinned `3.0`).
- `forge_doctor.api.SCAN_SCHEMA_VERSION` — re-export of
  `JSON_SCHEMA_VERSION`; `__all__` extended. Docstrings + `docs/api.md`
  now state the two-contract model explicitly.
- `tests/unit/test_api.py` — mini JSON-Schema validator (type/required/
  properties/items/const/enum/oneOf) plus:
  - `test_scan_report_validates_against_published_schema` — real
    `render_json` output validated against `SCAN_REPORT`.
  - `test_golden_snapshots_validate_against_published_schema` — every
    committed `golden/*/expected/*.json` validated against
    `GOLDEN_SNAPSHOT`.
  - `SCAN_SCHEMA_VERSION` pinned to `"3.0"` and to the renderer const.
- `.github/workflows/ci.yml` smoke step hardened: summary-keys +
  project-shape asserts, `schema contracts` list+dump, `explain`,
  `checks` — on the installed wheel across ubuntu/windows/macos.
- `docs/release.md` — v1.0 pre-flight checklist (hard gates, contract
  review, spec archival human-only, version-bump fields, tag + dispatch
  steps).
- `docs/roadmap.md` — v0.8–v1.0 platform-intelligence summary section.

## Verification

- `pytest tests/unit/test_api.py tests/unit/test_docs.py` — 17 pass
- Full suite, ruff, format, mypy — see run output
- Deliberately NOT done: version bump/tag — release is a maintainer
  decision (release.yml is workflow_dispatch-only).

## Open question (spec'd, not decided)

- Unify `SCHEMA_VERSION` (1.0) with the scan contract (3.0) at 1.0?
  Currently independent by design; unifying is itself breaking.

Stays in `active/` — archive only on explicit human acceptance.
