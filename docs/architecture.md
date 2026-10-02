# Forge Doctor — Architecture

## Goal

Deterministic, offline CLI that diagnoses data-engineering projects and emits
structured results (`CheckResult`) renderable as Rich console, stable JSON,
HTML, SARIF 2.1.0, or a compact agent bundle. `forge-doctor` is the
deterministic sensor; smarter/agentic consumers (Spark Forge, CI, GitHub
Code Scanning, LLM agents) sit downstream.

## Layers

```
cli/ (Typer: scan + findings commands; domain groups — iceberg/emr/
     databricks/athena/kafka/...; estate groups — workspace/fleet/history;
     gates — lab/golden/bench/policy/plugins; integrations — mcp/lsp)
    |
    v
core/service.py   ScanService: the one pipeline (plugins, profile, policy,
                  suppressions, baseline, cache) shared by CLI, MCP, LSP
    |
    v
core/runner.py  ----> plugins/discovery.py (external checks, SDK v2 identity)
    |
    v
checks/*        one file per category; each Check.run(ctx) -> list[CheckResult]
    |
    v
analyzers/*     semantic index (one ast.parse per file), domain project
                models + platform_graph_builder (canonical entity graph)
    |
    v
core/cache.py   user-cache dir (never the repo) — sha256-keyed per-file
                facts + dep_shas provenance; off by default in CI
    |
    v
core/context.py ProjectContext: root, file inventory, pyproject, git, env
    |
    v
output/*        console.py (Rich) / json_renderer.py / html_renderer.py /
                sarif_renderer.py / agent_renderer.py / summary.py
```

The platform-intelligence layer sits beside the check pipeline (it reads
`ProjectContext`, never the check runner):

- `core/platform_graph.py` — canonical `DataPlatformGraph`: typed entities
  (`compute_job`, `table`, `stream`, `workflow`…) + `reads/writes/produces/
  invokes` relationships; built by `analyzers/platform_graph_builder.py`.
- `core/capabilities.py` — versioned capability registry over the
  `knowledge/capabilities/` packs; tri-state-plus answers (`supported` /
  `conditional` / `unsupported` / `unknown` — never fabricated).
- `core/semantic_diff.py` + `core/change_intel.py` — entity diff + blast
  radius + risk for `diff --semantic`; capability transitions and
  version-move migration requirements from the `compatibility` packs.
- `core/migration.py` + `core/whatif.py` — deterministic migration plans
  and hypothetical-change evaluation.
- `core/workspace.py` + `core/fleet.py` — multi-repo `WorkspaceModel`
  (shared `merge_repos`), cross-repo `DEFINES/IMPLEMENTS/INVOKES` links,
  manifest-driven estate queries.
- `core/history.py` — `scan --record` snapshots under
  `.forge-doctor/history/`; `history`/`diff`/`trend` never re-scan.
- `core/contract.py` + `core/architecture*.py` — platform contract files
  and drift detection.
- `core/lab.py` / `core/golden.py` / `core/bench.py` — scenario suites,
  snapshot regression, performance budgets.
- `core/policy_pack.py` — organization policy packs (`extends`,
  `require_approval`), evidence bundles, named baselines.
- `core/remediation.py` + `core/rootcause.py` — deterministic fix plans
  and causal clustering over promoted findings.
- `core/runtime_evidence.py` — offline runtime artifacts as evidence
  (no target-code execution, no cloud calls).

Side modules (not in the check pipeline):

- `core/baseline.py` — save a scan as JSON and diff later scans against it
  (`is_new` tagging, `BaselineDiff` new/fixed/existing); format 2 requires
  matching `fingerprint_version`, anything else fails loudly.
- `core/service.py` — `ScanService`: the one pipeline (plugins, profile,
  policy, suppressions, baseline, cache) shared by CLI, MCP, and LSP.
- `core/fingerprint.py` — v3 semantic fingerprints:
  `check_id|file|symbol|evidence_anchor` (AST statement or normalized source),
  stable across line moves and message rewording.
- `core/policy.py` — policy-as-code: `[tool.forge-doctor.policy]` rule
  overrides + governed `[[suppressions]]` (scoped, owned, expiring); expired
  suppressions reactivate findings and emit POLICY001.
- `core/compat.py` — detected-environment + migration-risk report driven by
  the knowledge packs; IaC version pins feed detection.
- `core/knowledge.py` — versioned JSON knowledge packs under
  `forge_doctor/knowledge/{glue,spark,python,errors}/`: version status,
  runtime maps, compatibility changes, error signatures. schema_version 2
  carries pack_version/verified_at/sources; `knowledge verify` flags packs
  stale >90d. The engine stays stable while knowledge ships inside the wheel.
- `core/profiles.py` — severity re-mapping profiles
  (`default|strict|security|spark-performance|glue-migration|production`).
- `core/scaffold.py` — `forge-doctor init` project scaffolding.
- `core/project_info.py` — `forge-doctor info` stats; reads context, runs no
  checks.
- `core/cache.py` — incremental analysis cache (`--cache/--no-cache`,
  `forge-doctor cache`, `cache clean`); scan memoized on the context.
- `core/diagnose.py` — deterministic log fingerprinting via
  `knowledge/errors/` packs (substring + `re:` patterns, occurrence counts).
- `core/spark_runtime.py` — `spark eventlog|plan|logs`: executor loss, skew,
  spill, GC pressure, single-task stages, retries, scheduler delay, cartesian
  products, BNLJ, single-partition exchanges, global sorts, join-strategy mix.
  Stdlib only — no pyspark.
- `core/lineage.py` — static lineage graph (jobs ↔ datasets) from the index:
  spark.read.*, read.format().load(), spark.sql FROM/JOIN/INSERT, writeTo,
  saveAsTable, insertInto, write.<fmt>, Glue from_catalog. Renders text/json/
  dot/mermaid + OpenLineage-shaped JSON.
- `core/schema.py` — schema extraction (avsc/JSON Schema/SQL DDL/dbt yml) and
  classified diffs (compatible | potentially breaking | breaking; rename?
  detection) over files or `base...head` ranges.
- `core/graph.py` — Project Intelligence Graph: repo/job/dataset/infra/
  orchestrator nodes + contains/reads/writes/deploys/triggers edges;
  json/dot/mermaid.
- `core/sbom.py` — CycloneDX 1.5 for project deps, plugins, knowledge packs,
  forge-doctor itself.
- `integrations/mcp_server.py` — zero-dep JSON-RPC 2.0 stdio server
  (`initialize`, `tools/list|call`, `resources/list|read`, `ping`); handler is
  a pure `handle(dict) -> dict | None`.
- `integrations/lsp_server.py` — optional `pygls` server mapping findings to
  publishDiagnostics on open/change/save; mapping is unit-testable offline.

Dependency rule: nothing above imports anything below's siblings — checks never
see Typer/Rich; renderers never run analysis; context never executes target code.

## Check model

```python
class Severity(Enum):
    PASS | INFO | WARNING | ERROR


@dataclass(frozen=True)
class CheckResult:
    check_id: str  # stable: REP001, PY003, SPARK001 ...
    title: str
    severity: Severity
    category: str  # repository|python|spark|aws|iac|iceberg|streaming|... (see checks.md)
    message: str
    file: Path | None
    line: int | None
    recommendation: str | None
    # Finding Model v2 — optional, omitted from JSON when unset:
    column: int | None
    end_line: int | None
    end_column: int | None
    confidence: Confidence | None  # HIGH|MEDIUM|LOW — honesty for heuristics
    evidence: str | None  # the source line that triggered it
    tags: tuple[str, ...]  # performance|security|supply-chain|...
    docs_uri: str | None
    source: str | None  # plugin distribution; None = built-in
    fixable: bool | None
    fingerprint: str  # stable per-occurrence id (auto-derived)
    is_new: bool | None  # set only when --baseline is in use
```

`ScanReport.baseline` holds a `BaselineDiff(new, fixed, existing)` when a
baseline file was compared; `exit_code` then only counts findings where
`is_new` is true.

A check is a small class implementing the `Check` protocol
(`id`, `title`, `category`, `run(ctx) -> list[CheckResult]`). One check may emit
0..N results (e.g. every `collect()` site gets its own result). Severity PASS
represents a positive finding so the summary can count verified-good signals.

## Context

`ProjectContext` is built once per scan and memoizes: resolved root, file
inventory (pruned walk), parsed `pyproject.toml`, git facts (`ls-files`,
repo-ness via `git` CLI when present), project-local venv info, relevant env
vars, and user options. Checks read only from context — never re-read files.

## Registry & runner

`CheckRegistry` holds check instances keyed by id, supports category filtering
and id ignores. `CheckRunner.run(ctx)` executes selected checks sequentially,
isolating failures: an exception becomes an internal ERROR result
(`Unexpected error while running <id>`), expanded to a traceback only under
`--verbose`.

## Plugins

External packages register checks via the entry-point group
`forge_doctor.checks`. Each entry point resolves to a `Check` instance (or a
class/callable producing one). `plugins/discovery.py` loads them defensively —
a broken plugin degrades to a warning, never a crash. Designed for
`pipx inject forge-doctor forge-doctor-<ext>`.

Plugin SDK v2 stamps every loaded check with `__fd_identity__`
(`PluginIdentity`: distribution, version, api_version, entry_point) — the
allowlist keys on check id, distribution, or entry-point name, never class
names. `plugins list|validate|doctor` report per-plugin API compatibility and
load health; `PluginDescriptor` declares name/version/api_version/required
forge-doctor version/capabilities/checks.

Trust model: plugins run in-process with CLI privileges. `--no-plugins`
skips them; `[tool.forge-doctor.plugins].allow` in the scanned project's
pyproject restricts which may load; the runner stamps each plugin finding's
`source` with the distribution name.

## Diff & integrations

`forge-doctor diff` compares fingerprints across two saved reports, a report
and a git ref, or a `base...head` range — refs are scanned in temporary
detached worktrees. `action.yml` is a composite GitHub Action that scans and
uploads SARIF to Code Scanning; `.pre-commit-hooks.yaml` wires pre-commit's
per-file invocation to `--files`.

## Decisions

- **ast over regex** for Spark patterns — precise file:line, no import needed.
- **`packaging` lib** for PEP 440/441 specifier checks — stdlib can't do this.
- **`[project]` (PEP 621)** layout with Poetry 2.x; `poetry-core>=2.0` backend.
- **`[dependency-groups]` (PEP 735)** for dev deps; Poetry >=2.2 reads them.
- **Check ids are the API** — messages may change, ids never.
- **Severity, not score** — no arbitrary weights; PASS/INFO/WARNING/ERROR.
- **No writes** — Forge Doctor diagnoses; a future `fix` command would be new.
