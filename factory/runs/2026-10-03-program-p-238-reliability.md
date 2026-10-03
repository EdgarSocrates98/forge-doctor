# Run: Program P wave 4 — Reliability & SLA intelligence (spec 238)

- **Commit**: this commit
- **Date**: 2026-10-03
- **Spec**: `factory/specs/active/238-reliability-sla-intelligence.md`

## Scope

Reliability-behavior modeling: mechanism surface per subject, delivery
semantics, service objectives, freshness paths, failure domains,
REL001–REL010.

## Files changed

- `src/forge_doctor_data/core/reliability.py` — **new**:
  `EvidenceState` (declared/observed/absent/unknown), `Mechanism`,
  `ReliabilityModel` (13 mechanisms incl. delivery),
  `extract_reliability`/`observe_reliability` (runtime upgrades only
  on demonstrable facts), `ServiceObjective`/`extract_objectives`
  (sla_*/rpo/rto attrs), `FreshnessPath`/`freshness_paths` (PARTIAL
  when any hop lacks timestamps; total_lag None, never summed across
  incompatible hops), `FailureDomain`/`failure_domains` (topology
  attrs only), `DeliverySemantics`/`delivery_semantics`
  (at_most/at_least/effectively/exactly_once_claimed/unknown — full
  path required for claimed), `rel_findings` REL001–010,
  `engine_delivery_notes` (pack accessor).
- `src/forge_doctor_data/knowledge/reliability/engines.json` — **new**:
  delivery/checkpoint/dedup/dlq semantics per engine.
- `src/forge_doctor_data/cli/runtime.py` — `runtime reliability <root>`
  command (optional `--artifact` for runtime upgrades).
- `docs/checks.md` — REL### documented as runtime-scoped findings.
- `tests/unit/test_reliability.py` — **new**, 15 tests.

## Design decisions

- Negated attrs (`dlq="none"`) emit ABSENT — distinguishes "disabled"
  from "unevidenced"; missing attrs produce no mechanism (UNKNOWN is
  implicit, not listed).
- Exactly-once requires declared intent *and* retries + idempotency +
  dedup + checkpoint all evidenced — a bare flag stays UNKNOWN.
- REL005 compares declared RPO to scoped backup/checkpoint evidence —
  no cadence math without exported cadence.
- Failed-then-completed on one fingerprint upgrades retries/recovery
  to OBSERVED — the only runtime upgrade supported by exports.

## Validation

- `pytest tests/unit/test_reliability.py` — 15 passed
- `pytest tests/ -x -q` — full suite
- `mypy` — clean, 258 files
- `ruff check` + `ruff format --check` — clean
