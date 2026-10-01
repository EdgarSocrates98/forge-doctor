---
id: 171-platform-graph-model
title: Connected Data phase B1 - DataPlatformGraph canonical entity/relationship model
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_platform_graph.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase B. Today every domain model
is independent. The prompt prescribes a canonical graph so the engine
can answer "what does this Terraform change affect". Pure stdlib,
in-memory, deterministic — no graph database dependency. This spec is
the schema/store only; population comes in 172.

# Acceptance Criteria
- `core/platform_graph.py` (or `analyzers/platform_graph.py`):
  `EntityKind` enum — workflow, task, compute_job, query, dataset,
  table, stream, catalog, storage_location, principal,
  infrastructure_resource, database, graph, graph_node, graph_edge.
  `RelKind` enum — INVOKES, READS, WRITES, DEFINES, GOVERNS, STORED_IN,
  DEPENDS_ON, TRIGGERS, PRODUCES, CONSUMES.
- `Entity` (kind, stable id, name, domain provenance, file/line when
  known, attrs) and `Relationship` (src→dst, kind, evidence kind from
  169, attrs) frozen dataclasses. Ids follow
  ``{kind}:{domain}:{canonical_identifier}`` where the identifier
  prefers ARN > catalog-qualified name > path > bare name — so
  `workflow:airflow:orders` never collides with `table:iceberg:orders`.
  Deterministic, never derived from iteration order.
- `DataPlatformGraph`: add/query entities and relationships; neighbor,
  inbound/outbound, by-kind, reachability queries; dedup identical
  edges; cycle-safe BFS/DFS helpers intended for blast-radius consumers.
- Serializable to a stable plain-dict/JSON form (deterministic ordering)
  for future export.
- Tests: entity/edge invariants, dedup, traversal, determinism across
  insertion orders, serialization round-trip.

# Constraints
- Pure stdlib; no execution of analyzed code; no network.
- The graph is a model, not a persistence layer — keep the API minimal
  (build + query + serialize); no query language.
