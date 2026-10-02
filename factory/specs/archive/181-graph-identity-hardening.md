---
id: 181-graph-identity-hardening
title: Connected Data hardening - canonical table identity, streaming endpoints, semantic blast-radius
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_platform_graph_population.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
`prompt_evolucao4.md` review of HEAD `f244c54` — no P0, two P1s to land
before/with spec 173, one P2 pulled forward:

1. `table:sql:catalog.db.orders` and `table:iceberg:catalog.db.orders`
   are the same table but different nodes — producer must not alone own
   the canonical namespace.
2. `sink="iceberg"` has no identifier — 20 streams collapse to one
   `table:iceberg:iceberg` node. The streaming model needs real
   source/sink identifiers.
3. (P2) `blast-radius` is plain reachability — impact must follow edge
   semantics: DEPENDS_ON/READS/CONSUMES/STORED_IN traverse *inbound*
   (dependents of a changed target); INVOKES/DEFINES/WRITES/PRODUCES/
   GOVERNS/TRIGGERS *outbound*.

# Acceptance Criteria
- Deterministic catalog-aware table resolution: SQL/streaming table
  references whose first qualifier is a catalog configured with an
  iceberg impl (`spark.sql.catalog.<c> = *iceberg*`) resolve to
  `table:iceberg:<qualified-name>` — same node the iceberg adapter mints.
  Unresolvable references keep `table:sql:`/format domain. Never fuzzy.
- `StreamingQuery` gains `source_identifier`/`sink_identifier` extracted
  from literal `toTable`/`start`/`table`/`load` args and `option()`
  keys (`subscribe`/`subscribePattern`/`streamName`/`table`/`path`/
  `topic`). Non-literal args yield "" — never guessed.
- Unidentified endpoints become `dataset:<fmt>:<fmt>` (honest marker),
  never `table:<fmt>:<fmt>` fake-table nodes.
- `impact_reachable()` + blast-radius uses semantic directions; core
  `reachable()` unchanged.
- Adversarial coverage: non-catalog-qualified SQL stays `table:sql:`,
  non-iceberg catalogs don't claim, non-literal sink args mint nothing.
- Docs + CHANGELOG + run record.

# Constraints
- No changes to `core/platform_graph.py` core — policy lives in the
  consumer layer (`platform_graph_builder.py` / `cli/platform.py`).
- Every join requires observable identity (same catalog/name/ARN/path).
