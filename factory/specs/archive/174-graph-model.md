---
id: 174-graph-model
title: Connected Data phase D1 - GraphProjectModel + GRAPH modeling checks + graph CLI
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/checks/test_graph.py tests/unit/test_graph_model.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase D, part 1. Graph Intelligence
comes *before* Neptune — Neptune is one implementation of a graph
model. The model must distinguish the two paradigms the prompt calls
out: property graph (vertices/edges, both carry properties) vs RDF
(subject/predicate/object, Neptune quad S-P-O-G). Sources: openCypher /
Gremlin / SPARQL strings and calls in code, graph DDL files, bulk-load
CSV headers, graph-modeling config.

# Acceptance Criteria
- `analyzers/graph_model.py` `GraphProjectModel`: detected graph
  workloads; paradigm (property_graph|rdf|unknown); vertex labels, edge
  labels, properties, predicates; source evidence (file/line); schema
  reconstructed where statically visible.
- `checks/graph.py` (`category = "graph"`): GRAPH001 anchor; GRAPH002
  disconnected-component risk; GRAPH003 orphan vertex type; GRAPH004
  edge references undefined vertex type; GRAPH005 directionality
  inconsistency; GRAPH006 relationship redundantly modeled as property
  AND edge; GRAPH007 overly generic edge label (e.g. `RELATED`,
  `LINKS`); GRAPH010 graph model behaving like relational tables.
  GRAPH008/GRAPH009 (property fan-out, supernode) are data-dependent —
  INFO-gated or deferred with a documented reason.
- `cli/graph.py`: `graph inspect .` and `graph schema .` — vertices,
  edges, potential issues; severity-sorted findings via common renderer.
- `knowledge/graph/` packs: `property-graph.json`, `rdf.json`,
  `modeling.json` (schema 2 + sources).
- Tests per check + model + CLI; registration; docs + CHANGELOG.
- `tests/unit/adversarial/test_graph.py` — Definition of Done: every
  new semantic model ships adversarial fixtures (FP/FN/malformed).

# Constraints
- Graph facts are STATIC/CONFIG evidence; traversals deferred to 175.
- Never recommend "use a graph database" — the model reports structure;
  workload-fit advice is the 180 advisor's job.
