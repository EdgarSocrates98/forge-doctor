---
id: 107-data-quality
title: Avro nullable fix + real OpenLineage + SBOM v2
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_schema.py tests/unit/test_sbom.py tests/unit/test_lineage.py -q
  - python -m pytest -x -q
---

# Context
Three data-quality fixes from the review:
1. schema.py: `"default" in f` marks Avro fields nullable — wrong per spec
   (default only helps reader-side evolution).
2. `--format openlineage` emits a shaped dict, not a valid RunEvent.
3. sbom.py: only declared deps (lock used just for version fill), serial
   uuid5(path) varies by checkout dir, hardcoded vendor "cognition".

# Acceptance Criteria
- Avro: nullable = type is union containing "null" or type == "null".
  `default` presence does NOT imply nullable. Regression test.
- Schema output declares support level (e.g. `"parser": "best-effort"` in
  json) — honest, per docs.
- OpenLineage: emit spec-shaped RunEvents — eventType, eventTime, producer
  (URI), schemaURL, run.runId (deterministic uuid5 of job+project), job
  {namespace,name}, inputs/outputs {namespace,name}. Keep the internal
  format name honest.
- SBOM: all lock packages become components (transitives included) with a
  `dependencies` graph (root → declared → transitive); serialNumber
  derived from project NAME not path; no `vendor: cognition` — omit or use
  the real project author.
- Tests: avro default-not-nullable, openlineage fields present, sbom
  deterministic serial across paths + includes transitive dep.

# Constraints
- stdlib only; deterministic serial (uuid5 on name+version).
