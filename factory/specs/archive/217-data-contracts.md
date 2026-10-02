---
id: 217
title: Data contracts + schema evolution linting
agent: claude
risk: medium
verification:
  - python -m pytest tests/unit/ -k datacontract -x -q
  - python -m pytest tests/ -x -q
---

# Roadmap-4 Wave 2b - Data Contracts

## Context

The doc lists "data contracts, metrics, schema evolution" beside dbt.
Contract files (`datacontract.yml`, ODCS `*.odcs.yaml`, schema.yml
contracts) declare producers' promises: schema, SLA, quality terms,
owners. Lint them + detect breaking schema evolution across diffs.

## Acceptance Criteria

- `analyzers/datacontract_model.py` — `DataContractModel`: discovered
  contract files (datacontract-cli YAML, ODCS JSON/YAML), per-contract:
  id, servers, schema fields+types, SLA fields (availability,
  freshness, retention), quality terms, owner.
- `DCTR###` checks: `DCTR001` contract missing schema section;
  `DCTR002` contract missing SLA/freshness where model claims prod
  usage; `DCTR003` field type drift vs detected table schema (when a
  warehouse/dbt model knows the real table — cross-domain);
  `DCTR004` undocumented breaking change candidate in `diff --semantic`
  (field removed/type narrowed between refs).
- Schema-evolution: `diff` surfaces field add/remove/type-change on
  contract files and marks breaking changes (removed field, narrowed
  type) as high-risk with blast radius to consuming models.
- Lab suite `labs/datacontract/*`.

## Constraints

- Normalize to a minimal internal contract subset; do not re-implement
  the full datacontract-cli spec — record unsupported sections as
  parsed-but-unchecked (honest coverage).

## Open Questions

- Which formats are canonical targets (datacontract-cli vs ODCS)?
  Support both minimally; flag others as recognized-not-verified.
