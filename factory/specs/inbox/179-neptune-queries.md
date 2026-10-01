---
id: 179-neptune-queries
title: Connected Data phase F2 - Gremlin/openCypher/SPARQL analyzers + neptune explain
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_neptune_queries.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evo_neptune_graph_dynamo.md` Phase F, part 2. Neptune supports
three query families: Gremlin and openCypher for property graphs,
SPARQL 1.1 for RDF. Three lightweight analyzers extract query shape
from `*.gremlin`, `*.cypher`, `*.sparql`/`*.rq` files and from query
strings in code — feeding the traversal model (175) and Neptune checks.

# Acceptance Criteria
- `analyzers/neptune_queries.py`: `GremlinAnalyzer`,
  `OpenCypherAnalyzer`, `SPARQLAnalyzer` producing per-query records
  (language, file/line, pattern summary, selectivity signals, depth
  bounds, projection size). Queries flow into the 175 traversal model
  where shapes map cleanly.
- NEP020 MATCH/traversal without selective predicate; NEP021
  variable-length relationship unbounded (`[*]` / `*` / `repeat()`
  without `times()`); NEP022 cartesian graph pattern (disconnected
  MATCH clauses without relationship); NEP023 large result projection
  (`RETURN *`, no LIMIT on unbounded pattern); NEP024 property filter
  applied post-traversal. Applied per language where the shape is
  expressible.
- `cli/neptune.py` gains `neptune queries .`, `neptune schema .`,
  and `neptune explain <file>` / `analyze-explain` — parse a supplied
  Neptune explain/profile JSON and flag large intermediate cardinality,
  broad start, late filtering (offline; never connects to Neptune).
- `knowledge/neptune/query-languages.json` (schema 2 + sources).
- Tests per analyzer + check + explain fixture; docs + CHANGELOG.

# Constraints
- Analyzers are shape extractors, not full parsers — degrade to
  `unparsed` honestly like the SQL model does.
- Explain analysis consumes a user-provided file only; no endpoint
  calls.
