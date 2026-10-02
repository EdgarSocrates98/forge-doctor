# Getting Started

From zero to a gated CI scan in five steps. Everything below is offline,
deterministic, and safe — Forge Doctor never imports or executes your code.

## 1. Install

```bash
pipx install forge-doctor        # isolated CLI install (recommended)
# or: pip install forge-doctor   # into the current environment
```

Check the install:

```bash
forge-doctor doctor              # self-check: config, plugins, cache, git
forge-doctor version
```

## 2. First scan

```bash
cd your-project
forge-doctor scan .
```

Read the report top-down: each finding shows `rule-id severity file:line`,
the evidence line, a confidence, and a concrete recommendation. Nothing is a
mystery — ask the tool to explain itself:

```bash
forge-doctor explain SPARK001     # why it fires, when it is OK, how to fix
forge-doctor trace SPARK001 src/job.py:42   # why THIS occurrence fired
forge-doctor remediate .          # deterministic fix plans
forge-doctor fix .                # preview safety-classified text fixes (--apply writes safe only)
```

Rule ids are stable API: `SPARK001` means the same thing forever. Messages
may be reworded; ids and semantics never change.

## 3. Tame the noise honestly

Do not blanket-ignore — prefer the governed mechanisms:

```toml
# pyproject.toml of the scanned project
[[tool.forge-doctor.suppressions]]
rule = "SPARK001"
path = "src/legacy/**"
reason = "migration in progress"   # required context
owner = "@data"
expires = "2026-12-31"             # past expiry reactivates the finding
```

```bash
forge-doctor suppressions .        # audit: ACTIVE / EXPIRED / UNUSED
forge-doctor scan . --profile strict   # or a different severity profile
```

Organization-wide rules (`forbid this pattern, require that attribute`) ship
as policy packs — see `forge-doctor policy list` and `docs/checks.md`.

## 4. Gate CI without breaking on legacy debt

```bash
# save today's findings as the baseline
forge-doctor scan . --save-baseline .forge-doctor-baseline.json

# in CI: fail only on NEW findings
forge-doctor scan . --baseline .forge-doctor-baseline.json --fail-on warning
```

Exit codes: `0` clean, `1` findings at/above the fail threshold,
`2` internal/usage error. For PR review:

```bash
forge-doctor diff main...HEAD --semantic   # entity diff + blast radius + risk
```

SARIF upload to GitHub Code Scanning is one step via
[action.yml](../action.yml).

## 5. Pick your report format

```bash
forge-doctor scan . --format json      # machine contract (schema_version)
forge-doctor scan . --format sarif     # code scanning
forge-doctor scan . --format html      # shareable single file
forge-doctor scan . --format agent     # compact bundle for LLM consumers
forge-doctor schema contracts          # JSON Schemas for every artifact
```

## Where to go next

- **Command surface** — `README.md` usage block, `forge-doctor --help`.
- **Every rule** — `docs/checks.md` (id, severity, *when it is OK*, fix).
- **Domain intelligence** — `forge-doctor <domain> inspect .` for
  `iceberg`, `airflow`, `terraform`, `parquet`, `stepfunctions`,
  `streaming`, `controlm`, `dynamodb`, `neptune`, `lakeformation`, `emr`,
  `databricks`, `delta`, `athena`, `lambda`, `kafka`, `kinesis`, `flink`,
  `data-model`.
- **Runtime evidence** — `forge-doctor diagnose <log>`,
  `forge-doctor runtime inspect .`, `forge-doctor streaming diagnose .`
  — all offline, from exported artifacts.
- **Planning** — `forge-doctor what-if`, `forge-doctor migrate plan`,
  `forge-doctor capabilities list`.
- **Programmatic use** — `docs/api.md` (`forge_doctor.api` is the stable
  SDK surface; internals are not versioned).
- **Quality harnesses** — `forge-doctor lab run`, `forge-doctor golden run`,
  `forge-doctor bench run` for the engine's own correctness corpus.
- **Architecture** — `docs/architecture.md`.
- **Contributing** — `CONTRIBUTING.md`.
