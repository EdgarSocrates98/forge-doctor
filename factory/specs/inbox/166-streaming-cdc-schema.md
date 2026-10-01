---
id: 166-streaming-cdc-schema
title: Streaming stage 8 - CDC model + schema registry intelligence
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_streaming_cdc.py tests/unit/test_cdc_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_streaming.md` sub-cycle 8 of 10. CDC spans Debezium, DMS,
Kafka CDC, Lakeflow AUTO CDC, Delta CDF, Iceberg CDC-like patterns.
Schema governance: Glue Schema Registry / Confluent.

# Acceptance Criteria
- `CDCModel` evidence: op/before/after/sequence fields (Debezium-shape
  columns in schema/read evidence), `apply_changes`/`AUTO CDC` usage,
  MERGE-in-stream patterns, Delta CDF (`read_change_feed`/
  `table_changes`), primary-key/sequence-column evidence.
- `StreamingSchemaModel`: format (avro/json/protobuf), registry usage
  (`glue_schema_registry`, `SchemaRegistryClient`,
  `schema.registry.url`), subject/version/compatibility evidence,
  `from_avro`/`to_avro`/`from_protobuf` call sites.
- New checks: DBXCDC001 primary key missing in AUTO CDC (WARNING),
  DBXCDC002 ambiguous sequence column (INFO), DBXCDC003 out-of-order
  risk when ordering field absent (INFO), DBXCDC005 manual MERGE
  implementation where AUTO CDC fits (INFO); SCHEMASTR001 no schema
  governance evidence on typed payloads (INFO), SCHEMASTR003
  consumer/producer schema-version mismatch evidence (INFO),
  SCHEMASTR004 registry bypassed (manual `json`/string parse of
  registry-shaped payloads — INFO).
- knowledge packs: `knowledge/streaming/cdc.json`,
  `schema_registry.json`.
- Tests + docs + CHANGELOG.

# Constraints
- CDC fields matched as evidence patterns, never by executing
  deserializers.
- Compatibility claims need both sides' version evidence; else INFO
  "cannot verify".
- Depends on 159/165.
