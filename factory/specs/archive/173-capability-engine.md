---
id: 173-capability-engine
title: Connected Data phase C - Capability Engine (platform capability registry)
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_capabilities.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase C. Before rules like
`if dynamodb_global_table and ...` proliferate, platform capabilities
need a registry: each platform/variant declares capabilities and
limitations, and checks ask the registry instead of hardcoding service
facts. Seeds: DYNAMODB_TRANSACTIONS, DYNAMODB_STREAMS,
DYNAMODB_GLOBAL_TABLE_MREC, DYNAMODB_GLOBAL_TABLE_MRSC,
NEPTUNE_OPENCYPHER, NEPTUNE_GREMLIN, NEPTUNE_SPARQL, GRAPH_BULK_LOAD,
GRAPH_PROPERTY_MODEL, GRAPH_RDF_MODEL — plus capabilities for platforms
already modeled (Spark versions, Glue versions, Iceberg/Delta features).

# Acceptance Criteria
- `core/capabilities.py` or `analyzers/capabilities.py`:
  `Capability` registry keyed by platform (+ optional version/engine
  variant); `supports(platform, capability)` and
  `limitations(platform)` lookups; capabilities declared in knowledge
  packs (`knowledge/capabilities/*.json`, schema 2 + sources), not in
  code.
- Pack seeds covering DynamoDB (transactions/streams/global-table modes
  incl. MRSC no-transactions limitation), Neptune (gremlin/openCypher/
  SPARQL, property-graph vs RDF, bulk loader), and existing platforms
  where capability facts are already used by checks (migrate at least
  one hardcoded check fact to the registry as proof).
- Checks can query `ctx`/registry through one documented helper; no
  per-check JSON loading duplication.
- Tests: pack loading, lookup incl. version variants, unknown platform /
  capability honesty, migrated-check behavior unchanged.

# Constraints
- Registry is read-only facts; it answers "does X support Y" — it never
  emits findings by itself.
- Facts need `sources` in each pack; capability claims without a source
  are rejected at load.
