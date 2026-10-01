---
id: 010-diagnose
title: forge-doctor diagnose — deterministic log error fingerprinting
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_diagnose.py -q
  - cat fixture.log | python -m forge_doctor diagnose - 
---

# Acceptance Criteria
- knowledge/errors/{glue,spark,iceberg,lakeformation,databricks,python}.json: id,title,patterns[],causes[],related[],severity
- diagnose FILE or `-` (stdin); substring+regex match; aggregates multiple distinct errors; counts occurrences
- text output shows detected/causes/related; --format json emits findings
- offline, deterministic, no LLM
