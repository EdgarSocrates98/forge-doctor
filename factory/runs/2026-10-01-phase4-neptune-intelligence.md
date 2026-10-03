# Run record: phase 4 - Neptune Intelligence (specs 178 + 179 + 180)

Program: `prompt_evo_capability1.md` (4 phases / 4 atomic commits).
Phase 4 of 4 — final commit. Specs staged to `active/`; review is a
human gate.

## Delivered

- `analyzers/neptune_queries.py` — `GremlinAnalyzer`,
  `OpenCypherAnalyzer`, `SPARQLAnalyzer` wrapping the shared
  `graph_queries` extractors (no parser duplication). `neptune_queries`
  reuses `graph_model` traversals and adds honest `parsed=False`
  records for query-language files that yield zero traversals.
- `analyzers/neptune_explain.py` — offline explain/profile artifact
  analysis: classifies STATIC (text plan) / OBSERVED_METADATA
  (structured plan, no counters) / RUNTIME (execution counters
  present); flags large intermediate cardinality, broad starts, late
  filters. Never connects to Neptune.
- `analyzers/neptune_model.py` — `NeptuneProjectModel` with explicit
  `product` (DATABASE/ANALYTICS/UNKNOWN): Terraform `aws_neptune_*`
  (+ `aws_neptune_graph` = Analytics), CloudFormation `AWS::Neptune*`,
  boto3 `neptune`/`neptunedata`/`neptune-graph` bindings, endpoint
  strings (`*.neptune.amazonaws.com`, port, `DriverRemoteConnection`),
  `start_loader_job` kwargs (source/format/iamRoleArn/region/
  failOnError/parallelism/updateSingleCardinalityProperties), IAM-auth
  hints, manual-algorithm definitions.
- `checks/neptune.py` (`category="neptune"`): NEP001 anchor; NEP010
  language↔paradigm incompatibility resolved via the capability
  registry (DERIVED, official source carried); NEP020-024 query-shape
  checks; NEP030-033 ingestion checks; NEP040-045 infra checks
  (INFO-gated when topology isn't observable); NEPGT001-003 global
  database (pack-driven facts); NEPA001-002 analytics; NEPCD001
  DynamoDB-stream → Neptune-mutation idempotency risk.
- `cli/neptune.py` — `neptune inspect|schema|queries|ingest|explain|
  analyze-explain|compatibility`.
- `cli/datamodel.py` — `data-model inspect`: access-style percentages
  (key lookup / bounded query / scan / traversals) + neutral
  graph-oriented note, facts only.
- `platform_graph_builder` — `_dynamodb` adapter (table PRODUCES
  stream, stream TRIGGERS lambda via event-source mapping, QUERY
  READS/WRITES table) and `_neptune` adapter (`graph:neptune:<cluster>`
  entities, loader-job READS S3 / WRITES graph, QUERY edges, lambda
  WRITES via handler-module join). `aws_neptune_cluster` naming now
  prefers `cluster_identifier` so Terraform DEFINES converges on the
  model id.
- `knowledge/neptune/` — products, engines, ingestion, features,
  query-languages, bulk-loader, global-database, explain, analytics,
  compatibility (schema 2 + sources). Also repaired
  `knowledge/dynamodb/streams.json` (truncated JSON) — `knowledge
  verify` clean.
- Tests: `test_neptune_model.py`, `test_neptune_queries.py` (incl.
  explain artifacts), `checks/test_neptune.py`, `adversarial/
  test_neptune.py` (comment-only "neptune", aliased client, dynamic
  query, mixed paradigms, Analytics vs Database), and
  `test_connected_data.py` — multi-domain fixture rebuilding
  Terraform → DynamoDB → stream → Lambda → Neptune, blast-radius
  spanning domains, determinism, and the prompt's capability
  cross-tests (MRSC+transact UNSUPPORTED→DERIVED finding; openCypher
  on property_graph SUPPORTED; openCypher on rdf UNSUPPORTED).

## Verification

- `pytest tests/unit/test_neptune_model.py tests/unit/test_neptune_queries.py tests/unit/checks/test_neptune.py tests/unit/adversarial/test_neptune.py tests/unit/test_connected_data.py -q`: 85 passed
- `pytest -x -q`: 954 passed
- `mypy src`: 108 files clean; `ruff check src tests`: clean
- `forge-doctor-data knowledge verify`: all packs ok
- Smoke: `neptune inspect|queries|ingest|compatibility|explain`,
  `data-model inspect`, `platform blast-radius` on a multi-domain
  fixture — all render expected facts/findings.

## Notes / deviations

- Spec numbering (178/179) is authoritative: NEP030-033 ingestion,
  NEP040-045 infra. Prompt's broader families were added under
  NEPGT/NEPA/NEPCD ids.
- `neptune explain` keeps artifacts offline per spec; classification
  follows the EvidenceKind policy (RUNTIME only when execution
  counters are present).
- SPARQL `SELECT *` no longer counts as variable-length path (fixed in
  `graph_queries.parse_sparql`); bare `g.V()`/`g.E()` chains without
  args/vocab no longer count as traversals (aliased-client FP guard).
- Specs 178/179/180 remain in `factory/specs/active/` — archive is a
  human `--accepted` decision.
