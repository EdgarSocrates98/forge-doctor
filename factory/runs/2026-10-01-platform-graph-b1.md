# Run: connected-data phase B1 — DataPlatformGraph core (spec 171)

Per `prompt_evo_evolucao3.md`: review approved A1/A2 and green-lit the
graph. Conditions folded in: canonical ids, minimal API, evidence-tagged
edges, adversarial-fixture DoD added to specs 174/176/178.

## What shipped

- `core/platform_graph.py` — deliberately separate from `core/graph.py`
  (the repo-structure `forge-doctor-data graph` view): this is the canonical
  entity/relationship model.
- `EntityKind` (15): workflow, task, compute_job, query, dataset, table,
  stream, catalog, storage_location, principal, infrastructure_resource,
  database, graph, graph_node, graph_edge.
- `RelKind` (10): INVOKES, READS, WRITES, DEFINES, GOVERNS, STORED_IN,
  DEPENDS_ON, TRIGGERS, PRODUCES, CONSUMES.
- `Entity` — frozen; id = `{kind}:{domain}:{identifier}` so
  `workflow:airflow:orders` can never collide with
  `table:iceberg:orders`; adapters pick identifier by preference
  ARN > catalog-qualified > path > bare name. `attrs` is a sorted-able
  tuple-of-pairs (frozen-safe).
- `Relationship` — frozen+hashable; dedup via set; `evidence_kind` from
  169 rides each edge (STATIC/CONFIG/DERIVED per producing fact).
- `DataPlatformGraph` — `add_entity` (first-wins), `add_relationship`
  (rejects edges to unknown entities, dedups), `outbound`/`inbound`/
  `neighbors`, cycle-safe `reachable` BFS (`direction="out"|"in"`),
  deterministic `to_dict` (sorted entities + edges).

## Design conditions honored

- Minimal API — build + query + serialize; no query language/ORM.
- stdlib only; unidirectional adapters (models never import each other)
  — population lands in 172.
- Graph never infers capability: entity attrs describe what exists;
  "does X support Y" stays with the 173 capability engine.

## Verification

- `pytest tests/unit/test_platform_graph.py` — 11 passed
- `pytest -x -q` — 689 passed
- `mypy src` — 90 files clean · `ruff check` + `format --check` — clean
