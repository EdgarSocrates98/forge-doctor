# Run: spec 206 — Change Intelligence

**Spec:** `factory/specs/active/206-change-intelligence.md` (R3-P5)
**Result:** implemented + verified; spec left in `active/` for human review.

## What was built

- `core/change_intel.py` — `analyze_change(base_g, head_g)`:
  - `capability_diff` evaluates the capability registry per ref using
    observed per-domain versions (`VERSION_ATTRS` over entity attrs,
    most-common value per domain) and reports status transitions
    (`platform/CAP`, sorted by capability+platform). Unknown versions
    yield `unknown` — never fabricated.
  - `version_moves` detects `*_version`/`runtime`/`dbr` attr changes on
    entities present in both graphs.
  - `migration_requirements` matches each move to the domain
    `compatibility` pack's `targets[to_version]` — required changes +
    HIGH-severity blockers; absent coverage → `status: "unknown"`.
- `cli/diff.py` — `_semantic_diff` renders "Capability transitions"
  table + "Migration requirements" block in text mode; `-f json` adds
  `capabilities` + `migration_requirements` keys (plus the existing
  risk/changes payload). Exit code unchanged: fails only on new
  findings or high semantic risk.
- `cli/fleet.py` — `_VERSION_ATTRS` now shared from `change_intel` so
  fleet and diff agree on which attrs carry versions.

## Constraints honored

- No parallel compat logic: versions/severity come from the same
  `compatibility` packs that `migrate`/`compatibility` commands load;
  capability states come from `capability_registry()`.
- Read-only, deterministic ordering (sorted transitions + moves).
- No version move → empty sections; unknown target version → `unknown`.

## Verification

- `pytest tests/unit/ -k "semantic_diff or change_intel"` — 21 pass
  (9 new `test_change_intel` cases: transitions both directions,
  unchanged, no-move empty, unknown never fabricates, glue 4->5
  blockers, deterministic ordering, identical/removed-entity edges).
- `pytest tests/integration/test_cli.py -k diff` — 4 pass, incl. new
  e2e over a real git range asserting the JSON keys and text sections.
- ruff check / ruff format / mypy — clean.

## Notes / non-decisions

- Capability granularity is per-domain (most common observed version),
  matching `history`'s per-platform census; entity-level condition
  attrs (e.g. `format_version`) stay conditional rather than resolved —
  consistent with fleet's honest UNKNOWN policy.
- `--format` is accepted only with `--semantic`; saved-report diffs
  remain text-only (spec scopes JSON to the semantic report).
