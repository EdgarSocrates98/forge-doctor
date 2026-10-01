---
id: 110-cache-semantic-signature
title: Dep semantic signatures - transitive cache invalidation
agent: devin
risk: high
grill: completed
verification:
  - python -m pytest tests/unit/test_cache.py -q
  - python -m pytest tests/unit/test_index.py tests/unit/test_lineage.py -q
  - python -m pytest -x -q
---

# Context
Cache entries record `dep_shas` = sha256 of each direct dep's file content.
Transitive hole: in job.py -> reader.py -> source.py, changing source.py's
semantics (e.g. `load()` stops returning a DataFrame) re-parses reader.py,
but reader.py's file sha is unchanged so job.py keeps stale cached buckets
even though the semantics it imported changed.

# Acceptance Criteria
- Each dep record carries the dep's *export signature*: a deterministic
  hash over the dep's derived/exported semantic state (at minimum the
  exported functions' `returns_df` + return facts after producer
  propagation), not only its file bytes.
- A dep whose export signature changed invalidates the dependent's cached
  facts exactly like a dep sha change does today (dependent re-parsed and
  analyzer buckets recomputed).
- Transitive: new test proves A -> B -> C where mutating C so an exported
  fn loses `returns_df` invalidates A even though B's content sha is
  unchanged.
- A dep change that does not move the export signature (comment-only edit)
  does NOT invalidate dependents - test proves no re-parse.
- `ANALYZER_SCHEMA_VERSION` bumped; cache module docstring updated.

# Constraints
- Stdlib only, deterministic signature (sorted/canonical serialization),
  no network. Restore+propagate ordering must not regress cold-scan
  correctness.

# Review Notes
- ? Open product question (parked, NOT this spec): plugin trust default -
  today empty `trusted`+`allow` loads everything. Reviewer suggests a
  future `plugins.mode = "explicit"` for governed/CI environments. Threat-
  model decision needs human call before any behavior change.
