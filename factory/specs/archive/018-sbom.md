---
id: 018-sbom
title: forge-doctor-data sbom — CycloneDX output
agent: devin
risk: low
grill: completed
verification:
  - python -m pytest tests/unit/test_sbom.py -q
---

# Acceptance Criteria
- components: pyproject deps (+lock versions when poetry.lock parses), installed plugins, knowledge packs, tool itself
- CycloneDX 1.5 JSON: bomFormat/specVersion/metadata/components; --format cyclonedx (default) or text summary
