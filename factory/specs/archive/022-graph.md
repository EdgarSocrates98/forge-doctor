---
id: 022-graph
title: Project Intelligence Graph — forge-doctor-data graph
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_graph.py -q
---

# Acceptance Criteria
- nodes: repo, jobs(modules w/ spark/glue), datasets(lineage), infra(IaC resources), orchestrators(airflow DAG files)
- edges: reads/writes/deploys/triggers
- --format json|dot|mermaid; JSON is the agent-facing contract w/ schema_version
