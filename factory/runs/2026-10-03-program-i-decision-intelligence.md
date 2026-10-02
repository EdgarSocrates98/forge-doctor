# Run: Program I — Decision Intelligence (spec 228)

- **Initial HEAD**: `f43a5f9`
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/228-decision-intelligence.md`
  (authored this session)

## Scope

Ranked, explainable action list over existing signals — findings,
clusters, remediation plans, fix classes, policy, blast radius.
Automate ranking, not decisions.

## Files changed

- `src/forge_doctor/core/decisions.py` — **new**: `Advice` model +
  `advise()` ranking. Score = documented constant terms
  (severity 100/40, confidence 30/15/5, cluster 10 + size*5 +
  confirmed 30, fix 20/10/0, plan 15, policy 40, blast radius min
  10*3); `score == sum(breakdown)` is a test-pinned invariant.
- `src/forge_doctor/cli/advise.py` — **new**: `advise <path>`
  (text table w/ breakdown rows, `-f json`, `--top N`).
- `src/forge_doctor/cli/__init__.py` — registration (Estate & change
  panel).
- `tests/unit/test_decisions.py` — **new**, 11 tests.
- `README.md`, `CHANGELOG.md` — entries.

## Design decisions

- **Cluster keys are `check_id:fingerprint` refs** — `related_findings`
  is keyed by check id after `split(":", 1)`; cluster membership maps
  per check, not per fingerprint.
- **Confirmed-cluster boost** replaces a "root cause" boost:
  `root_causes` holds evidence texts, not finding refs, so the honest
  signal is `PromotionLevel.CONFIRMED` (runtime-corroborated chain).
- **Blast radius is cluster-gated** — entity attribution only exists
  via clusters today; unlinked findings get an explicit `unknowns`
  note instead of fabricated entity citations.
- **One row per check_id** — fingerprints aggregate; `count` reports
  occurrence count. Deterministic tie-break: score, then smallest
  fingerprint, then check_id.

## Tests / gates

- `pytest -k "decision or advise"`: **11 passed**
- `pytest test_docs.py`: command documented in README (enforced).
- ruff/mypy on touched files: clean.
- Dogfood on a tmp fixture: deterministic ranked table; scoring
  breakdown printed per row.

## Known limitations

- Unlinked findings (no cluster) carry no entity/blast evidence —
  `unknowns` says so; deeper finding→entity attribution is separate
  work.
- Scoring weights are module constants (documented, not configurable)
  per the spec's open question.

## Open questions

- `advise` vs `--baseline` new-finding prioritization — deferred.
- Spec remains in `active/` pending human review.
