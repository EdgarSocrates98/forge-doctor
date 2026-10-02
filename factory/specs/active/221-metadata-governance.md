---
id: 221
title: Catalog/metadata/governance adapters (DataHub, OpenMetadata)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "metadata or datahub or openmetadata" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 4 - Catalog & governance intelligence

## Context

Doc wave 4 = metadata/governance. DataHub/OpenMetadata exports
(`datahub` CLI JSON dumps, OpenMetadata entity JSON, ingestion recipes)
describe the *declared* estate — the interesting findings are
**declared-vs-actual drift**: datasets in the catalog that no longer
exist, tables in the graph missing from the catalog, undocumented
producers, missing owners/glossary terms.

## Acceptance Criteria

- `analyzers/metadata_model.py` — `MetadataEstateModel`: catalog vendor,
  datasets (urn, platform, schema fields if present), owners, glossary
  terms, tags, lineage edges, ingestion recipes (source types only —
  never secrets).
- Declared-vs-actual drift checks (`META###`): `META001` cataloged
  dataset with no corresponding entity in `DataPlatformGraph` (stale
  catalog entry); `META002` platform entity absent from catalog
  (coverage gap — informational); `META003` dataset without owner;
  `META004` dataset without description/tags on prod-flagged assets;
  `META005` lineage edge in catalog contradicting observed graph
  (declared upstream ≠ detected upstream).
- Adapters: DataHub (`*.datahub.json`, aspects arrays), OpenMetadata
  (`*.ometa.json`/entity exports), AWS Glue Data Catalog
  (`glue-catalog` observed exports → reuse existing glue entities),
  Unity Catalog (`unity` observed exports) — last two minimal: presence
  + coverage signals only.
- `catalog inspect .` CLI + lab suites + adversarial cases.

## Constraints

- Never ingest credentials from ingestion recipes — parse connector
  *types* only and redact connection blocks.
- Drift findings must state which side (declared vs actual) drives the
  finding; both directions are findings, not auto-fixes.

## Open Questions

- Is "coverage gap" (META002) too noisy at scale? Default severity
  `info` and grouped counts rather than per-entity findings beyond N=20.
