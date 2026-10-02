# Run record: phase 3 - DynamoDB Intelligence (specs 176 + 177)

Program: `prompt_evo_capability1.md` (4 phases / 4 atomic commits).
Phase 3 of 4. Specs staged to `active/`; review is a human gate.

## Delivered

- `analyzers/dynamodb_model.py` — `DynamoDBProjectModel` +
  `DynamoDBTable`/`DynamoDBAccess`/`DynamoDBStream`/`DynamoDBIndex`.
  Static only. Tables detected from Terraform `aws_dynamodb_table`
  (nested attribute/GSI/LSI blocks, billing mode, TTL, stream spec,
  replica regions for global tables), CloudFormation
  `AWS::DynamoDB::Table`/`GlobalTable`, and Python `boto3`
  bindings (`resource/client("dynamodb")`, `.Table(name)` restricted to
  receivers proven to be DynamoDB resources — arbitrary `x.Table()`
  does not bind). Access ops extracted from the call index +
  enclosing-symbol AST kwargs: `get_item`/`put_item`/`update_item`/
  `delete_item`/`query`/`scan`/`batch_*`/`transact_*`, key literals,
  projections/filters, `ConsistentRead`, enclosing function.
  Stream consumers: `aws_lambda_event_source_mapping` + CFN
  `EventSourceMapping` wired back to tables; idempotency evidence via
  `batchItemFailures` returns. Single-table entity prefixes from key
  literals and key-builder f-strings.
- `checks/dynamodb.py` — `category="dynamodb"`: DDB001 anchor,
  DDB002 scan in handler/latency path, DDB003 full-scan without
  projection/filter, DDB004 query missing sort-key condition,
  DDB005 hot-partition candidate (data-dependent wording, INFO),
  DDB006 GSI projection sparse-vs-load, DDB007 unused-looking GSI,
  DDB008 on-demand+provisioned mix, DDB009 LSI candidate,
  DDB010 TTL missing where entities look ephemeral; stream family
  DDBSTR001 stream enabled w/o consumer, DDBSTR002 consumer w/o
  idempotency evidence, DDBSTR003 batchItemFailures ignored,
  DDBSTR004 keys-only image for consumers needing attributes,
  DDBSTR005 global-table fan-out per region; global/capacity checks
  DDBGLB001-003 via the capability registry (global table region
  variant support, MRSC idiom) and DDBCAP001-002 throughput signals.
  Severity honest: static facts WARNING/INFO, data-dependent claims
  stay INFO. Global-table capability decisions resolve through
  `ctx.capabilities` (provenance carried into findings).
- `cli/dynamodb.py` — `forge-doctor dynamodb inspect|access-patterns|
  indexes|streams|global-tables|capacity <path>`.
- `knowledge/dynamodb/` — indexes, limits, modeling, streams,
  transactions, global-tables (schema 2 + sources); `knowledge verify`
  clean.
- `docs/checks.md` DynamoDB section; CHANGELOG entry.
- Tests: `test_dynamodb_model.py`, `checks/test_dynamodb.py`,
  `test_dynamodb_streams.py`, `adversarial/test_dynamodb.py`
  (non-dynamodb `.Table()` receivers, string-literal lookalikes,
  malformed blocks — no false positives asserted).

## Verification

- `pytest tests/unit/test_dynamodb_model.py tests/unit/checks/test_dynamodb.py tests/unit/test_dynamodb_streams.py tests/unit/adversarial/test_dynamodb.py -q`: 54 passed
- `pytest -x -q`: 869 passed
- `mypy src`: 102 files clean; `ruff check src tests`: clean;
  `ruff format`: applied
- Smoke: `forge-doctor dynamodb inspect <fixture>` renders table,
  GSI, stream, global-table (1 region), entity prefix; findings show
  DDB002/DDBSTR005/DDB001/DDB003 as expected for the fixture.

## Notes / deviations

- `.Table(...)` binding hardened mid-implementation after a smoke
  fixture showed arbitrary receivers would bind; now requires DynamoDB
  resource provenance or a `dynamodb` dotted path.
- Stream↔table association had to be computed before the module loop;
  otherwise stream checks could not see table-level evidence.
- Git-Bash `/tmp` is not the Windows-visible temp dir for `poetry run`
  Python — smoke fixtures must live under the workspace or a real
  Windows path.
- Specs 176 + 177 remain in `factory/specs/active/` — archive is a
  human `--accepted` decision.
