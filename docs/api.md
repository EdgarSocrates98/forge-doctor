# Public API & stability contract

Forge Doctor exposes a small, deliberately narrow SDK surface via
`forge_doctor.api`. Everything listed in `api.__all__` follows the
package's semver; everything else is internal and may change without
notice.

```python
import forge_doctor.api as fd

report = fd.scan("./my-project")  # ScanReport
graph = fd.platform_graph("./my-project")  # DataPlatformGraph
fd.capabilities_evaluate("glue", "GLUE_ICEBERG", version="5.0")
reports = fd.what_if("./p", {"glue-version": "5.1"})
plans = fd.migrate_plans("./p")
```

## Stable surface

| Name | Returns | Notes |
|------|---------|-------|
| `scan(path, *, profile, ignore)` | `ScanReport` | Full check pipeline + policy |
| `platform_graph(path)` | `DataPlatformGraph` | Canonical entity/edge model |
| `capabilities_evaluate(platform, capability, *, version, attributes)` | `str` status | `supported`/`unsupported`/`conditional`/`unknown` |
| `what_if(path, changes)` | `list[WhatIfReport]` | Deterministic change simulation |
| `migrate_plans(path)` | `list[MigrationPlan]` | Advisory plans only — never mutates |
| `version()` | `str` | Installed package version |
| `SCHEMA_VERSION` | `str` | Contract version for the artifact family (lineage/graph/workspace/policy/misc payloads) |
| `SCAN_SCHEMA_VERSION` | `str` | Contract version of `scan --format json` reports |
| `ScanReport`, `ScanOptions`, `DataPlatformGraph` | types | Re-exported for annotations |

## Versioning rules

- **Package version** (semver): breaking changes to names in
  `__all__`, required arguments, or returned dataclass field removals
  bump MAJOR; new functions and additive fields bump MINOR.
- **Two schema contracts exist** — they version independently:
  - `SCAN_SCHEMA_VERSION` (`"3.0"`) — the `scan -f json` report
    (`tool`/`project`/`summary`/`results`), pre-dating the SDK surface.
  - `SCHEMA_VERSION` (`"1.0"`) — every other `--format json` payload
    (lineage, graph, workspace, policy, command `meta` blocks).
- In both: new keys are MINOR-compatible; removing/renaming keys or
  changing value types requires a MAJOR bump.
- Check IDs and canonical entity ids (`{kind}:{domain}:{identifier}`)
  are stable identifiers — they never depend on message text.

## JSON contracts

`forge-doctor schema contracts` lists the published JSON Schemas;
`forge-doctor schema contracts <name>` dumps one:

- `scan-report` — `scan -f json` / `diff` JSON payloads
- `policy-pack` — `.forge-doctor/policy/*` files
- `lab-expected` — `labs/**/expected.json` ground truth
- `golden-snapshot` — `golden/*/expected/*.json` snapshots

## Guarantees

- Offline only: no network, no cloud calls, no target-code execution.
- Deterministic: identical inputs → identical findings, graph, plans.
- Non-destructive: `migrate_plans` and `what_if` are advisory; nothing
  writes back to the scanned project (scan cache lives in
  `.forge-doctor/` and is the only write).
