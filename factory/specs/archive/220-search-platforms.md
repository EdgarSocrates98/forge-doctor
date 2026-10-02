---
id: 220
title: Search/indexing platforms (OpenSearch, Elasticsearch)
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k "search or opensearch or elastic" -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 3c - Search / indexing

## Context

Doc wave 3 lists OpenSearch/Elasticsearch. Evidence: index templates /
mappings / settings JSON, ISM/ILM policies, ingest pipelines, Terraform
`aws_opensearch*`/`elasticsearch_*` resources, cluster settings exports.

## Acceptance Criteria

- `analyzers/search_model.py` — `SearchPlatformModel`: vendor
  (opensearch|elasticsearch), domains/clusters, indices + templates with
  mappings (field types), shards/replicas, ISM/ILM policies, ingest
  pipelines, snapshots.
- `OS###`/`ES###` check ids — prefer one prefix family `SRCH###` if both
  vendors share rules (open question below); minimum checks: prod index
  template without replica shards; wildcard/`logs-*` index with no ISM
  rollover/retention; mapping with unbounded `fields` explosion risk
  (many `object` without `enabled:false` on known-verbose keys);
  Terraform domain without encryption-at-rest / node-to-node TLS.
- CLI `search inspect .`; lab suites; adversarial case (unrelated JSON
  with `"mappings"` key must require stronger evidence — template/index
  shape + vendor signal).
- Capability pack: vector/kNN support per vendor+version, ISM vs ILM
  split, serverless variants.

## Constraints

- High false-positive risk on generic JSON: require compound evidence
  (file name conventions + key shape) before attributing vendor.

## Open Questions

- One shared prefix (`SRCH`) vs per-vendor (`OS`/`ES`): pick shared
  prefix with vendor in message, since most rules are common — confirm
  at grill.
