# Forge Ecosystem Contracts

Forge Doctor is the deterministic evidence engine of the Forge family.
Downstream tools (Spark Forge, API Forge, and future members) consume its
outputs without rescanning. This page is the interop contract.

## Guarantees

- **Deterministic** — identical project state produces byte-identical
  output. Bundles carry no timestamps or random ordering; entities,
  relationships, findings, and plans are sorted.
- **Stable ids** — `check_id` and `fingerprint` are stable identities:
  `check_id` never depends on message text, `fingerprint` is a semantic
  per-occurrence key (id|file|symbol|anchor) safe for joins and diffs.
- **Evidence, not guesses** — findings declare `evidence_kind`
  (`static`/`config`/`observed_metadata`/`runtime`/`derived`); capability
  statuses are `supported|unsupported|conditional|unknown`. `unknown` is
  honest, never fabricated.
- **Versioned** — `schema_version` follows the public SCHEMA_VERSION;
  the bundle also carries `contract` + `contract_version` for the bundle
  shape itself. Additive fields bump MINOR; removed/renamed fields bump
  MAJOR. Breaking changes require a new contract version.

## The contracts

`forge-doctor contracts list` lists them; `forge-doctor schema
contracts <name>` dumps the JSON Schema (draft 2020-12).

| name | artifact |
|---|---|
| `scan-report` | `scan --format json` — full scan report |
| `finding` | one result row (CheckResult serialization) |
| `evidence` | `runtime inspect --json` — normalized runtime model |
| `platform-graph` | `DataPlatformGraph.to_dict()` — entities + relationships |
| `capability-report` | `capabilities list --json` — platform → cap → status |
| `remediation-plan` | `remediate --json` — one remediation plan |
| `policy-pack` | org policy pack file |
| `lab-expected` | Forge Lab `expected.json` ground truth |
| `golden-snapshot` | golden-repo snapshot files |
| `handoff-bundle` | `export --format handoff` — the cross-tool bundle |

## The handoff bundle

```bash
forge-doctor export . --format handoff -o bundle.json
forge-doctor contracts verify bundle.json
```

Shape (stable keys, deterministic ordering):

```json
{
  "contract": "handoff-bundle",
  "contract_version": 1,
  "schema_version": "3.0",
  "tool": {"name": "forge-doctor", "version": "…"},
  "project": {"name": "…", "root": "…"},
  "summary": {"passed": 0, "info": 0, "warnings": 0, "errors": 0},
  "results": [ …findings sorted by (check_id, fingerprint)… ],
  "graph": {"entities": […], "relationships": […]},
  "capabilities": {"platform": {"CAPABILITY": "status"}},
  "plans": [ …remediation plans sorted by id… ]
}
```

A downstream tool receives entities, findings, capabilities, unknowns
(absence is honest — fields are present but may be empty), and
remediation candidates without rescanning.

## Verifying bundles

`forge-doctor contracts verify <bundle> [--contract <name>]` validates
an artifact against the published contract — `<bundle>` may be a file
path or `-` for stdin (the default when omitted). The validator covers
`type`/`required`/`properties`/`items`/`enum`/`const`/`oneOf`/
`additionalProperties`/`pattern` — a subset sufficient for the published
contracts, offline, with no `jsonschema` dependency. Exit 1 on
violations. Other Forge tools should call this in their own conformance
tests.

## Boundaries

- Contracts describe what the engine already emits — no new runtime
  behavior is invented for the contract's sake.
- No shared package yet: contracts live in `forge_doctor.core.schemas`
  until a second Forge tool exists to pin cross-repo requirements.
