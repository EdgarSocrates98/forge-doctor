# Fleet / estate intelligence

`forge-doctor-data fleet` answers org-scale questions across a manifest of
repositories — where `workspace` discovers nested projects inside one
tree, `fleet` merges an explicit list of repos (or a parent dir) into
one estate graph.

## Manifest

```yaml
# fleet.yml — paths are relative to the manifest file
fleet: my-estate
repos:
  - ./ingestion
  - path: ./analytics
    name: analytics-team        # optional display name
  - ../shared/terraform
```

```yaml
# alternative: workspace discovery over a parent directory
fleet: org-workspace
root: ../repos
```

JSON is accepted too (`{"repos": ["./a", "./b"]}` or a bare list).
Passing a directory instead of a file runs workspace discovery over it.
Remote fetching is deliberately out of scope — manifests point at local
checkouts.

## Commands

```bash
forge-doctor-data fleet inspect fleet.yml         # repos + merged graph census
forge-doctor-data fleet query fleet.yml runtimes  # entities by domain + versioned attrs
forge-doctor-data fleet query fleet.yml capability ICEBERG_MERGE_WRITE
forge-doctor-data fleet query fleet.yml dependents "*orders-etl*"
forge-doctor-data fleet query fleet.yml findings "SPARK*"
forge-doctor-data fleet report fleet.yml [-f json]
forge-doctor-data fleet portfolio fleet.yml [-f json]   # estate portfolio facts
forge-doctor-data fleet regressions fleet.yml [-f json] # shared regression dimensions across repos
```

- `portfolio` — estate facts (platforms with lifecycle status from
  capability/runtime packs, workloads, logical datasets and their
  physical representations, teams, environments, complexity counts,
  duplication signals classified `OPPORTUNITY`) plus the §7.2 answers:
  engines serving the same workload, datasets on multiple platforms,
  platforms carrying critical workloads, deprecated/EOL technologies,
  cross-cloud edge concentration, owners of cross-platform
  dependencies. Facts only — there is deliberately no health score.
- `regressions` — replays each repo's `.forge-doctor-data/execution-history/`
  into fingerprint series and reports regression dimensions shared by
  >=2 repos (e.g. the same queue regression fleet-wide after a platform
  upgrade).

- `query runtimes` — entity counts per domain plus a versioned-entity
  table (`version`, `glue_version`, `dbr`, `runtime`, …).
- `query capability <id>` — evaluates every entity against the
  capability registry (`supported`/`conditional`/`unsupported`
  buckets); entities whose platform has no facts are skipped.
- `query dependents <glob>` — everything in the merged graph that can
  break when matching entities change (dependency-aware traversal,
  cross-repo `repo:` links included).
- `query findings <check-id|glob>` — per-repo scan results matching the
  check id, grouped by repo (built-in checks only — deterministic, no
  plugins).
- `report` — estate census: repos, entities by kind/domain, findings by
  severity/category/repo, cross-repo link count.

## Scale

Tested at fixture scale (~10 repos). Cost is linear in repo count —
each repo builds its own platform graph; `report` and `query findings`
additionally run a built-in-check scan per repo. Hundreds of repos is
the aspiration, not a tested floor.
