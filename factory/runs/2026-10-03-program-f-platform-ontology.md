# Run: Program F — Platform Ontology (spec 225)

- **Initial HEAD**: `f2b1c48`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/225-platform-ontology.md` (authored
  this session — first consolidation spec; roadmap order: after 211,
  before roadmap-4 wave 1)

## Scope

Canonical documented vocabulary for entity kinds, relationship kinds,
evidence planes, evidence domains, producer domains, and capability
families — plus conformance enforcement.

## Files changed

- `src/forge_doctor/core/ontology.py` — **new**: `Term` registry,
  definitions for all 16 `EntityKind` + 11 `RelKind` + 5 `EvidenceKind`
  members, 17 evidence domains (imported from `incremental.py`), 28
  producer domains, runtime-derived capability families, `vocabulary()`
  (stable JSON shape), `validate_graph()`.
- `src/forge_doctor/cli/misc.py` — `ontology` group: bare `ontology`
  prints the vocabulary (`-f json`), `ontology validate <path>` checks
  graph conformance.
- `src/forge_doctor/cli/__init__.py` — `ontology` panel membership.
- `src/forge_doctor/core/schemas.py` — `platform-graph` contract pins
  `kind`/`evidence_kind` `enum`s to the ontology vocabulary.
- `docs/ontology.md` — **new**: vocabulary tables + versioning rules.
- `tests/unit/test_ontology.py` — **new**, 13 tests.
- `CHANGELOG.md`, `README.md`, `docs/getting-started.md`,
  `docs/roadmap.md` — entries.

## Design decisions

- **Enums execute, ontology documents** — `EntityKind`/`RelKind`/
  `EvidenceKind` remain the runtime enums; ontology maps every member
  to a definition and a completeness test fails on any undocumented
  term. The runtime-checkable drift surface is the free-text
  `domain` segment — that's what `validate_graph` enforces.
- **`ontology validate` is a standalone diagnostic**, not a scan-time
  check (spec open question resolved this way): domain violations are
  adapter-authoring problems, surfaced best as a gate — exit 1 with
  named violations.
- **Contract enums** — `platform-graph` pins `kind`/`evidence_kind`
  `enum`s; additive vocabulary changes are additive contract changes.

## Tests / gates

- `pytest -k ontology`: **13 passed**
- `pytest test_contracts.py test_api.py`: **27 passed** (bundle still
  validates under the tightened enums)
- ruff/mypy: clean
- Dogfood: `ontology validate .` on this repo initially **failed with
  23 violations** — two real vocabulary gaps found (`aws` generic
  provider domain, `neptune_loader` task domain); added to the
  vocabulary, now clean: 127 entities / 78 relationships conform.

## Known limitations

- `validate_graph` checks producer domains only — kind/rel/plane
  conformance is enforced by the enums at construction.
- Capability families are runtime-derived from bundled packs; they
  carry no definitions (pack docs own that).

## Open questions

- Plugin-defined namespaced kinds — deferred per spec.
- Whether ontology terms should get `since` version metadata.
- Spec remains in `active/` pending human review.
