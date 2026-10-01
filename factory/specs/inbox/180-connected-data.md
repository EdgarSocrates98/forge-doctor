---
id: 180-connected-data
title: Connected Data phase G - cross-domain graph edges + data-model advisor
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_connected_data.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase G (capstone). Wire the new
domains into the DataPlatformGraph (171/172) so the engine can
represent chains like DynamoDB →(stream)→ Lambda →(writes)→ Neptune,
and surface workload-shape facts without prescribing product decisions.
The prompt is explicit: report detected access styles; never emit
"migrate to Neptune".

# Acceptance Criteria
- `platform_graph_builder` adapters for graph/neptune/dynamodb models:
  table/database/graph entities; DynamoDBStream TRIGGERS Lambda;
  Lambda/code entities WRITES NeptuneGraph / DynamoDBTable; Terraform
  DEFINES Neptune/DynamoDB resources; graph_node/graph_edge entities
  from the 174 schema.
- `cli/datamodel.py`: `forge-doctor data-model inspect .` — detected
  access-style breakdown (key lookups vs bounded queries vs scans vs
  multi-hop traversals) as percentages of observed access ops; a
  neutral "graph-oriented access pattern detected" informational line
  when multi-hop traversal is first-class — phrased as a fact +
  evaluation prompt, never a migration directive.
- `platform blast-radius` answers spanning the new domains
  (e.g. Terraform → DynamoDB table → stream → Lambda → Neptune).
- Determinism tests incl. multi-domain fixtures; docs + CHANGELOG.

# Constraints
- Advisor output = facts + "consider evaluating" phrasing; prohibited:
  unrequested platform recommendations, superiority claims.
- All edges carry EvidenceKind; cross-domain joins stay deterministic.
