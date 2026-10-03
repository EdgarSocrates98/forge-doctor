---
id: 172-platform-graph-population
title: Connected Data phase B2 - populate DataPlatformGraph from domain models + platform CLI
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
`prompt_evo_neptune_graph_dynamo.md` Phase B, part 2. With the canonical
graph from 171, each existing domain model becomes a producer: Airflow
DAGs/tasks → workflow/task + DEPENDS_ON; Control-M jobs → task +
DEPENDS_ON; Step Functions machines/states → workflow/task + INVOKES;
Streaming queries → stream + CONSUMES/PRODUCES; SQL statements → query +
READS/WRITES; Iceberg/Parquet → table/storage_location + STORED_IN;
Terraform resources → infrastructure_resource + DEFINES.

# Acceptance Criteria
- `analyzers/platform_graph_builder.py`: per-domain adapter functions
  mapping model facts to entities/relationships; each edge carries the
  EvidenceKind of its source fact.
- Cross-domain joins where statically evident (e.g. Terraform
  `aws_sfn_state_machine` → the SFN machine it defines; Glue/EMR job
  names matching code entrypoints) — only deterministic joins, no fuzzy
  matching.
- `cli/platform.py`: `forge-doctor-data platform graph .` — counts by
  entity/relationship kind; `--json` deterministic export;
  `forge-doctor-data platform blast-radius <entity>` reachability listing.
- Registration + docs + CHANGELOG.
- Tests: adapters per domain, cross-domain join, CLI output, determinism.

# Constraints
- Absence of a model in a project must not break the build (adapters
  no-op on empty models).
- Joins between domains require observable identity (same name/ARN/path)
  — never guessed similarity.
