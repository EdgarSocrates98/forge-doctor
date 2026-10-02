---
id: 111-plugin-trust-adversarial-tests
title: Adversarial tests for the pre-load plugin trust boundary
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_plugins.py -q
  - python -m pytest -x -q
---

# Context
Spec 102 established the pre-load gate: `trusted`/identity entries in
`allow` are evaluated from entry-point metadata BEFORE `ep.load()`, so an
untrusted plugin never executes code. Existing tests cover the basic
trusted/untrusted paths; adversarial edges are unasserted.

# Acceptance Criteria
- Test: entry point trusted by *distribution* name loads even when its
  entry-point name differs (and vice versa: ep-name match loads when dist
  name differs).
- Test: mixed batch - two entry points, one trusted one not: trusted
  loads, untrusted's `ep.load()` spy is never invoked, untrusted reported
  via `infos` with "untrusted" status, no error entries.
- Test: `allow` entry that matches the check-id regex (e.g. "PLUGIN001")
  is NOT treated as an identity even if a distribution happens to share
  that name - pre-load gate stays open per legacy rule but no identity
  trust is granted by it.
- Test: untrusted plugin whose entry point resolves to a
  descriptor-producing *callable* is rejected pre-load - callable is never
  invoked (spy).
- Existing plugin tests keep passing.

# Constraints
- Test-only change; no production behavior change.
- Keep the current empty-config default (loads installed plugins) - the
  secure-by-default mode is a parked product decision (see 110 notes),
  not something to implement here.
