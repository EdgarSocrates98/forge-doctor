# Run record: phase 2 - Graph Intelligence (specs 174 + 175)

Program: `prompt_evo_capability1.md` (4 phases / 4 atomic commits).
Phase 2 of 4. Specs staged to `active/`; review is a human gate.

## Delivered

- `analyzers/graph_queries.py` — shared, engine-agnostic shape
  extractors for openCypher, Gremlin, and SPARQL (reused by the Neptune
  domain in phase 4). Static only: nothing executes; unrecognized input
  degrades to `parsed=False`. `GraphTraversal` records language, start
  selectivity, steps, directions, hop count/bounds, first filter
  position, projection size/star, result bound, writes, vertex/edge
  labels, properties, edge endpoints, predicates.
- `analyzers/graph_model.py` — `GraphProjectModel` + `GraphWorkload`:
  paradigms (`property_graph`/`rdf`/`unknown`), workloads from
  `.cypher/.cql/.opencypher/.sparql/.rq/.gremlin` files, RDF data files
  (.ttl/.nt/.n3/.rdf), Neptune bulk-load CSV headers (`~id/~from/~to/
  ~label`), and Python call sites (Gremlin `g.V()` chains via the call
  index inside-out `dotted` form; query strings passed to client
  methods). Aliased roots guarded by traversal-vocabulary evidence.
- `checks/graph.py` — `category="graph"`, spec numbering:
  GRAPH001 anchor, GRAPH002 disconnected components, GRAPH003 orphan
  vertex, GRAPH004 edge with undefined endpoint vertex type, GRAPH005
  direction inconsistency, GRAPH006 relationship as edge+label,
  GRAPH007 generic edge label, GRAPH008/GRAPH009 INFO-gated property
  fan-out + supernode candidates (data-dependent per spec), GRAPH010
  relational-shape artifacts; GRAPH020-025 traversal family
  (unselective start, unbounded result, unbounded variable-length,
  high fan-out, late filtering, repeated pattern); extensions GRAPH026
  (full-graph starts) and GRAPH030 (mixed paradigms). No ERROR severity;
  no cost claims without runtime evidence.
- `cli/graph.py` — `forge-doctor graph inspect|schema|traversals`.
  The pre-existing `forge-doctor graph <path> --format` dump
  (project-intelligence graph) is preserved via a fallback group and
  remains reachable explicitly as `graph project`.
- `knowledge/graph/` — property-graph, rdf, modeling, algorithms
  (schema 2 + sources); `knowledge verify` clean.
- `analyzers/platform_graph_builder.py` — graph adapter emits
  GRAPH/GRAPH_NODE/GRAPH_EDGE entities wired to FILE entities.
- `docs/checks.md` Graph section; CHANGELOG entry.
- Tests: `test_graph_model.py`, `checks/test_graph.py`,
  `test_graph_traversals.py`, `adversarial/test_graph.py`
  (comments/strings/malformed/mixed-paradigm fixtures).

## Verification

- `pytest tests/unit/checks/test_graph.py tests/unit/test_graph_model.py -q`: green
- `pytest tests/unit/test_graph_traversals.py -q`: green
- `pytest tests/unit/adversarial/test_graph.py -q`: green
- `pytest -x -q`: 815 passed
- `mypy src`: 99 files clean; `ruff check src tests`: clean;
  `ruff format --check`: clean
- Smoke: `graph inspect|traversals` render the model + findings;
  legacy `graph <path> --format json` still dumps the project graph.

## Notes / deviations

- Spec 174 names `cli/graph.py` + `graph inspect|schema`; spec 175 adds
  `graph traversals` — implemented as a `graph` group with a fallback
  preserving the pre-existing `forge-doctor graph <path>` dump
  (documented deviation; the prompt's `graph-data` sketch is superseded
  by the spec contract).
- Spec 174 requires `tests/unit/adversarial/test_graph.py` — used that
  name (not `test_graph_model.py`).
- GRAPH008/009 are INFO-gated with the spec's documented reason
  (data-dependent; no cardinalities without runtime data).
