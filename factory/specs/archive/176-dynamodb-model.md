---
id: 176-dynamodb-model
title: Connected Data phase E1 - DynamoDBProjectModel + access-pattern checks + dynamodb CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_dynamodb.py tests/unit/test_dynamodb_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase E, part 1. DynamoDB is
access-pattern-first modeling, not "simple key-value". Sources: boto3
client calls (GetItem/Query/Scan/BatchGetItem/BatchWriteItem/
TransactWriteItems), key expressions, table definitions in
Terraform/CFN (`aws_dynamodb_table`), and entity key patterns
(`USER#123` PK/SK conventions).

# Acceptance Criteria
- `analyzers/dynamodb_model.py` `DynamoDBProjectModel`: tables (name,
  source plane — code/tf/cfn), partition/sort keys where declared,
  GSIs/LSIs, access operations per table (op kind, key condition,
  projection/filter presence), capacity mode, streams flag, TTL flag,
  transactions usage, pk/sk literal patterns (e.g. `f"USER#{id}"` →
  prefix pattern + variable part).
- `checks/dynamodb.py` (`category = "dynamodb"`): DDB001 anchor;
  DDB002 Scan on a latency-sensitive path; DDB003 Scan without
  projection/filter strategy; DDB004 partition key likely poor
  cardinality (constant/near-constant PK); DDB005 hot-partition
  candidate (single static PK across writes); DDB006 constant partition
  key literal; DDB007 time-only sort key write pattern; DDB008 GSI
  duplicating base-table access; DDB009 GSI partition key likely hot;
  DDB010 GSI count vs observed access patterns. why/when_ok/fix each;
  static-risk framing ("static hotspot risk", not capacity math).
- `cli/dynamodb.py`: `dynamodb inspect .`, `access-patterns`,
  `indexes` — tables, keys, ops per table, GSI inventory, findings.
- `knowledge/dynamodb/`: `indexes.json`, `transactions.json`,
  `limits.json`, `modeling.json` (schema 2 + sources).
- Tests per check + model + CLI; registration; docs + CHANGELOG.
- `tests/unit/adversarial/test_dynamodb.py` — Definition of Done: every
  new semantic model ships adversarial fixtures (FP/FN/malformed).

# Constraints
- Hot-partition findings are static-risk heuristics — never claim
  throttling without runtime metrics.
- Streams/global-tables depth is spec 177; this spec flags the flags.
