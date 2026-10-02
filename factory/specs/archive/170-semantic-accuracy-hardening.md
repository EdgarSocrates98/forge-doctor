---
id: 170-semantic-accuracy-hardening
title: Connected Data phase A2 - adversarial fixtures + accuracy hardening for existing models
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/adversarial -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase A, deliverable 2. The eight
semantic models (SQL, Iceberg, Airflow, Control-M, Terraform, Parquet,
Step Functions, Streaming) were built on happy-path fixtures. The
sqlglot 27→28 reserved-word regression showed parser-version and
identifier drift can silently zero out a model. Before the graph and
DynamoDB expansion, each model needs adversarial coverage.

# Acceptance Criteria
- `tests/unit/adversarial/` suite covering, per existing model domain:
  false-positive cases (code that resembles but isn't the domain),
  false-negative cases (real usage the model should catch — dynamic
  receivers, aliased imports, cross-file assignment, chained calls),
  and malformed/truncated inputs that must degrade gracefully rather
  than raise.
- Parser-version contract tests: SQL fixtures avoid identifiers whose
  tokenization changed across supported sqlglot versions (>=26,<29);
  a test documents which identifiers are version-sensitive.
- Cross-file semantics tests: assignment/import chains split across
  files resolve (or are honestly unresolved) per each model's documented
  behavior.
- Each adversarial finding either passes or is recorded in the run
  record as a documented model limitation — no silent skips.
- Run record summarizing per-domain gaps found and fixed vs documented.

# Constraints
- Tests assert *documented* behavior; where a gap is found and fixable
  in scope, fix it; where it's architectural, record it — do not weaken
  assertions to hide gaps.
- No new runtime dependencies; fixtures are inline or under
  tests/fixtures following existing conventions.
