---
id: 177-dynamodb-streams-global
title: Connected Data phase E2 - DynamoDB streams, global tables, single-table + capacity CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_dynamodb_streams.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase E, part 2. Extends 176.
DynamoDB Streams feed Lambda/Kinesis/CDC pipelines; global tables now
have MREC and MRSC modes with materially different transaction
semantics (MREC: transactions atomic only in the originating region,
not replicated as a unit; MRSC: transactions unsupported). These facts
belong in the capability registry (173) plus dedicated checks.

# Acceptance Criteria
- Model extensions: `DynamoDBStreamModel` (view_type, detected consumers
  — Lambda event sources / Kinesis adapter / SFN, ordering,
  idempotency signals) and `DynamoGlobalTableModel` (mode
  MREC|MRSC|unknown, regions, consistency signals).
- `checks/dynamodb.py` additions: DDBSTR001 stream enabled, no consumer
  detected; DDBSTR002 consumer side-effects lack idempotency signal;
  DDBSTR003 duplicate-processing risk; DDBSTR004 retention/recovery
  mismatch; DDBSTR005 replicated events on global tables cause
  duplicate downstream effects. DDBGT001 multi-region write-conflict
  risk; DDBGT002 MREC transaction semantics (region-local atomicity)
  surfaced where transactions used; DDBGT003 transactions on MRSC
  (unsupported — ERROR via capability registry); DDBGT005 region
  routing strategy unclear.
- Single-table reconstruction: `SingleTableEntityModel` deriving entity
  types from `PK=X#{id}` / `SK=Y#{id}` patterns; `dynamodb inspect`
  shows the entity tree; `dynamodb streams`, `dynamodb global-tables`,
  `dynamodb capacity` CLIs.
- `knowledge/dynamodb/streams.json`, `global-tables.json` (schema 2 +
  sources, incl. MREC/MRSC semantics).
- Tests per check + mode variants; docs + CHANGELOG.

# Constraints
- Single-table vs multi-table is NOT a recommendation — report detected
  structure only (prompt: "não vou dizer single-table sempre é melhor").
- DDBGT003 must resolve through the capability registry, not a literal.
