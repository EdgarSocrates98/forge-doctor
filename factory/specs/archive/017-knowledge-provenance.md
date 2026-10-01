---
id: 017-knowledge-provenance
title: Knowledge pack provenance + knowledge commands
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_knowledge.py -q
---

# Acceptance Criteria
- packs gain schema_version 2, pack_version, verified_at, sources[]
- `forge-doctor knowledge list|info DOMAIN|verify` (verify = structure + staleness >90d warning)
- backward-compatible loaders for v1 packs
