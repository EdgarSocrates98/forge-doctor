# Run: Program D — Knowledge Supply Chain (spec 210)

- **Initial HEAD**: `fa2c866`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/210-knowledge-supply-chain.md`

## Scope

Authoring/validation/publish pipeline for knowledge packs — packs stay
data; no fetching, no network.

## Files changed

- `src/forge_doctor_data/core/knowledge.py` — added `scaffold_pack`,
  `write_scaffold`, `diff_packs`, `load_pack_ref`, `conformance`,
  `publish_checklist`, `bump_pack`, `SCAFFOLD_KINDS`.
- `src/forge_doctor_data/cli/misc.py` — `knowledge new|diff|test|publish`
  commands on the existing group; `import json` fix + dead-import
  cleanup.
- `tests/unit/test_knowledge_chain.py` — **new**, 24 tests.
- `CHANGELOG.md` — Added entry.

## Design decisions

- **Semantic diff** indexes sections by key/`id` (dicts and id-keyed
  lists); changed entries report leaf-level `path: old -> new` rows.
  Meta fields excluded.
- **Conformance** = per-pack structural/provenance checks (same rules
  as `verify_pack`, evaluated on the payload so synthetic packs work)
  + error entries (id, non-empty patterns, `re:` compile, every
  `examples` entry matches ≥1 pattern) + capability assertions
  re-evaluated per declared version through the real
  `CapabilityRegistry` (declared-vs-evaluated mismatch only flagged
  for unconstrained entries).
- **Positive-fixture rule**: a plain-substring pattern matches its own
  literal text, so entries with ≥1 substring pattern always have a
  derivable fixture — warnings are reserved for `re:`-only entries
  lacking `examples`. Bundled packs: 0 issues, 13 warnings (re:-only
  entries to enrich over time).
- **`knowledge new`** writes under the bundled `knowledge/` root by
  default (`--dir` overrides), refuses overwrites.
- **`knowledge publish`** is a checklist + optional local
  `pack_version`/`verified_at` bump (`--bump`); no remote publish.

## Tests / gates

- `pytest tests/unit/ -k knowledge -x -q`: **33 passed** (incl. prior
  knowledge tests)
- `ruff check`/`format`, `mypy` on touched files: clean
- Dogfood: `knowledge test` → 0 issues / 13 warnings;
  `knowledge publish glue` → ready, next_pack_version shown;
  `knowledge new demopack --kind errors` scaffolds a conformant pack.

## Known limitations

- `load_pack_ref` resolves bundled packs via the `load_pack` cache —
  diffing a just-written bundled pack may need a fresh process.
- `diff` on non-indexable sections falls back to whole-value compare.

## Open questions

- The 13 `re:`-only error entries without examples are real debt —
  enriching them is a follow-up data task, not blocking.
- Spec remains in `active/` pending human review.
