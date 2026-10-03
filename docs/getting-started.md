# Getting Started

From zero to a gated CI scan in five steps. Everything below is offline,
deterministic, and safe — Forge Doctor Data never imports or executes your code.

## 1. Install

```bash
pipx install forge-doctor-data        # isolated CLI install (recommended)
# or: pip install forge-doctor-data   # into the current environment
```

Check the install:

```bash
forge-doctor-data doctor              # self-check: config, plugins, cache, git
forge-doctor-data version
```

## 2. First scan

```bash
cd your-project
forge-doctor-data scan .
```

Read the report top-down: each finding shows `rule-id severity file:line`,
the evidence line, a confidence, and a concrete recommendation. Nothing is a
mystery — ask the tool to explain itself:

```bash
forge-doctor-data explain SPARK001     # why it fires, when it is OK, how to fix
forge-doctor-data trace SPARK001 src/job.py:42   # why THIS occurrence fired
forge-doctor-data remediate .          # deterministic fix plans
forge-doctor-data fix .                # preview safety-classified text fixes (--apply writes safe only)
```

Rule ids are stable API: `SPARK001` means the same thing forever. Messages
may be reworded; ids and semantics never change.

## 3. Tame the noise honestly

Do not blanket-ignore — prefer the governed mechanisms:

```toml
# pyproject.toml of the scanned project
[[tool.forge-doctor-data.suppressions]]
rule = "SPARK001"
path = "src/legacy/**"
reason = "migration in progress"   # required context
owner = "@data"
expires = "2026-12-31"             # past expiry reactivates the finding
```

```bash
forge-doctor-data suppressions .        # audit: ACTIVE / EXPIRED / UNUSED
forge-doctor-data scan . --profile strict   # or a different severity profile
```

Organization-wide rules (`forbid this pattern, require that attribute`) ship
as policy packs — see `forge-doctor-data policy list` and `docs/checks.md`.

## 4. Gate CI without breaking on legacy debt

```bash
# save today's findings as the baseline
forge-doctor-data scan . --save-baseline .forge-doctor-data-baseline.json

# in CI: fail only on NEW findings
forge-doctor-data scan . --baseline .forge-doctor-data-baseline.json --fail-on warning
```

Exit codes: `0` clean, `1` findings at/above the fail threshold,
`2` internal/usage error. For PR review:

```bash
forge-doctor-data diff main...HEAD --semantic   # entity diff + blast radius + risk
```

SARIF upload to GitHub Code Scanning is one step via
[action.yml](../action.yml).

## 5. Pick your report format

```bash
forge-doctor-data scan . --format json      # machine contract (schema_version)
forge-doctor-data scan . --format sarif     # code scanning
forge-doctor-data scan . --format html      # shareable single file
forge-doctor-data scan . --format agent     # compact bundle for LLM consumers
forge-doctor-data schema contracts          # JSON Schemas for every artifact
forge-doctor-data ontology                  # canonical vocabulary + `ontology validate .`
forge-doctor-data export . -f handoff       # portable bundle; `contracts verify` gates it
```

## Where to go next

- **Command surface** — `README.md` usage block, `forge-doctor-data --help`.
- **Every rule** — `docs/checks.md` (id, severity, *when it is OK*, fix).
- **Domain intelligence** — `forge-doctor-data <domain> inspect .` for
  `iceberg`, `airflow`, `terraform`, `parquet`, `stepfunctions`,
  `streaming`, `controlm`, `dynamodb`, `neptune`, `lakeformation`, `emr`,
  `databricks`, `delta`, `athena`, `lambda`, `kafka`, `kinesis`, `flink`,
  `data-model`.
- **Runtime evidence** — `forge-doctor-data diagnose <log>`,
  `forge-doctor-data runtime inspect .`, `forge-doctor-data streaming diagnose .`
  — all offline, from exported artifacts.
- **Planning** — `forge-doctor-data what-if`, `forge-doctor-data migrate plan`,
  `forge-doctor-data capabilities list`.
- **Programmatic use** — `docs/api.md` (`forge_doctor_data.api` is the stable
  SDK surface; internals are not versioned).
- **Quality harnesses** — `forge-doctor-data lab run`, `forge-doctor-data golden run`,
  `forge-doctor-data bench run` for the engine's own correctness corpus.
- **Architecture** — `docs/architecture.md`.
- **Contributing** — `CONTRIBUTING.md`.
