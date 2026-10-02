---
id: 175-graph-traversals
title: Connected Data phase D2 - GraphTraversalModel + traversal checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_graph_traversals.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase D, part 2. Extend the graph
model (174) with traversal extraction: starting point, hops, edge types,
filters, direction, depth, projection — from Gremlin chains, openCypher
patterns, and SPARQL paths found in code.

# Acceptance Criteria
- `GraphTraversal` records on `GraphProjectModel`: language, starting
  point (selective vs unselective), step/hop list, direction, bounded
  vs unbounded depth, filter presence/position, projection size.
- GRAPH020 traversal without selective starting point (e.g. `g.V()` /
  `MATCH (n)` with no label/property filter); GRAPH021 unbounded
  traversal; GRAPH022 recursive/variable-length path without depth
  bound (`*1..` missing); GRAPH023 high-fanout pattern (unfiltered
  multi-hop over generic edge types); GRAPH024 filter applied after
  traversal instead of at the start; GRAPH025 repeated identical
  traversal pattern across call sites.
- `cli/graph.py` gains `graph traversals .` — traversal inventory with
  per-traversal shape summary.
- Tests per check incl. at least one Gremlin, one openCypher, and one
  SPARQL fixture; registration; docs + CHANGELOG.

# Constraints
- Detection is static: report traversal *shape*, never estimated cost
  (no cardinalities without runtime data).
- Reuse the streaming/callsite machinery (`dotted` chains) rather than
  re-parsing Python independently.
