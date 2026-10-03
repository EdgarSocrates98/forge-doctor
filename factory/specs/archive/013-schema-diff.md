---
id: 013-schema-diff
title: Schema & contract doctor — forge-doctor-data schema diff
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_schema.py -q
---

# Acceptance Criteria
- sources: .avsc, JSON Schema .json, SQL DDL CREATE TABLE, dbt schema.yml (yaml via optional pyyaml extra; degrade w/ INFO if missing)
- `schema diff OLD NEW` files, or `schema diff base...head` git range (worktree compare of schema files)
- classify: column add (nullable=compatible, required=potentially breaking), drop (breaking), type change (widening map compatible else breaking), nullability tighten (breaking), rename (breaking)
- text table + --format json
