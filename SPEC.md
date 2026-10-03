# SPEC — forge-doctor-data v0.7

## §G goal
Deterministic CLI that diagnoses data-engineering projects (repo/python/deps/git/spark-ast/aws). pipx-installable, Poetry-managed. `forge-doctor-data scan .` → structured CheckResults → Rich|JSON.

## §C constraints
- Python >=3.11 | Poetry 2.x + poetry-core | Typer | Rich | pytest | ruff | mypy
- stdlib-first; no heavy deps; no DB, no web API, no agents, no cloud calls
- spark checks via `ast`, never import/execute analyzed code, no PySpark dep
- never print secrets (env/credentials/.env contents); AWS checks local-only
- deterministic: same project + same version → same results
- ~20 good checks, low false-positive; weak evidence → INFO not ERROR
- no health score in v0.1 (passed/info/warnings/errors only)
- no PyPI publish without explicit approval
- name decided: `forge-doctor-data` (renamed from `data-doctor` after user picked the ecosystem-paired alternative; the old CLI name collided with `vision-data-doctor` on PyPI and `data-doctor-cli` on crates.io). PyPI `forge-doctor-data` + `forge_doctor_data` verified free (404).

## §R research
- R1|poetry 2.x|PEP 621 [project] table native; tool.poetry deps deprecated mostly; poetry-core>=2.0 backend|python-poetry.org/blog/announcing-poetry-2.0.0
- R2|poetry groups|PEP 735 [dependency-groups] supported by Poetry 2.2+; fallback tool.poetry.group.dev|python-poetry.org/docs/pyproject
- R3|typer|current 0.27.x; [project.scripts] points at Typer app obj|pypi.org/project/Typer
- R4|pypi name|pypi.org/pypi/forge-doctor-data/json → 404 free; prior name `data-doctor` was free too but its CLI collided with vision-data-doctor|pypi.org
- R5|entry points|importlib.metadata.entry_points(group=...) stable API py>=3.10|docs.python.org

## §I interfaces
- cli: `forge-doctor-data scan [path=.]` flags: --check CAT* --ignore ID* --format text|json|jsonl|html|sarif|agent --quiet --fail-on error|warning --verbose --baseline PATH --save-baseline PATH --new-only --output PATH --watch --no-color --files F* --no-plugins --profile NAME --emit FMT[:PATH]* --show-root --cache/--no-cache --stats
- profiles: default|strict|security|spark-performance|glue-migration|production; `[tool.forge-doctor-data.policy] extends` picks the profile unless the CLI overrides
- cli: `forge-doctor-data repo|python|dependencies|spark|aws|docker|glue|ci|iac` = scan scoped to category
- cli: `forge-doctor-data plugins [list|validate|doctor]` | `version` | `checks` | `explain ID [--json]` | `init` | `info` | `diff OLD [NEW]` (reports, refs, or base...head) | `compatibility [--from V --to V]` | `workspace [--path D] [scan|diff base...head]` | `cache [--path D|clean]` | `suppressions [--json]` | `lineage [--format text|json|dot|mermaid|openlineage]` | `schema diff OLD [NEW]` | `migrate glue [--from --to]` | `knowledge [list|info D|verify]` | `sbom [--format cyclonedx|text]` | `graph [--format json|dot|mermaid]` | `doctor` | `diagnose FILE|-` | `trace ID FILE:LINE` | `spark [eventlog|plan|logs FILE]` | `iceberg [inspect|maintenance|compatibility|merge|files]` | `controlm [inspect]` | `airflow [inspect]` | `terraform [inspect]` | `parquet [inspect]` | `stepfunctions [inspect]` | `streaming [inspect]` | `mcp` | `lsp` | `--version` | `--install-completion`
- exit codes: 0 clean | 1 errors found (or --fail-on level hit; with --baseline only NEW findings count; diff/schema-diff exit 1 on new/breaking) | 2 internal/usage error
- json v3: {tool:{name,version}, schema_version:"3.0", version, project:{name(,root?via --show-root)}, summary:{...}, results:[{check_id,title,severity,category,message,file,line,recommendation,fingerprint,symbol?,is_new?} + optional {column,end_line,end_column,confidence,evidence,evidence_kind,tags,docs_uri,source,fixable}], baseline?:{new,fixed,existing}, suppressions?:[{rule,path,reason,owner,expires,status,matched}]}
- sarif: 2.1.0 runs[0] {tool.driver{rules}, results[{ruleId,level,message,rank,partialFingerprints,fixes?,locations?}]} — PASS excluded
- agent: {tool,version,summary,findings:[{id,sev,loc,fp}]} — compact, PASS excluded
- baseline file: .forge-doctor-data-baseline.json — {format:2, version, project, created, results:[{check_id,fingerprint,file,line,message}]} (format 1 still loads; v3 fingerprints won't match it)
- cache: .forge-doctor-data/cache/scan-cache.json — {format:1, files:{relpath:{sha256,facts}}}; per-file analyzer facts reused across scans
- fingerprints: `v3|check_id|file|symbol|evidence_anchor` — semantic, stable across line moves/message rewording; duplicate anchors get a deterministic ordinal
- config: `[tool.forge-doctor-data]` in target pyproject: exclude=[globs], ignore=[IDs], [tool.forge-doctor-data.plugins].allow=[check-ids|dist|entry-point], [tool.forge-doctor-data.policy] {extends,rules[ID]={severity,enabled}}, [[tool.forge-doctor-data.suppressions]] {rule,path?,line?,reason,owner,expires?}
- knowledge packs: `forge_doctor_data/knowledge/{glue,spark,python,errors}/*.json` shipped in wheel — version status/compat maps + error signatures; schema_version 2 with pack_version/verified_at/sources; `knowledge verify` flags packs stale >90d
- plugin group: entry_points `forge_doctor_data.checks` → Check instances; `__fd_identity__` PluginIdentity(distribution,version,api_version,entry_point); api 1+2 accepted; findings stamped source=<dist>
- check protocol: `id:str, title:str, category:str, run(ctx)->list[CheckResult]`; optional class attrs why/when_ok/fix/tags/confidence/docs_uri
- check ids: REP### PY### DEP### GIT### SPARK### AWS### DOCKER### GLUE### CI### IAC### POLICY### RT### SQL### ICE### CTM### AIR### TF### PARQ### SFN### STREAM### — stable, message-independent
- mcp: JSON-RPC 2.0 stdio; `mcp --root DIR` sandboxes tool paths; initialize negotiates protocolVersion (2024-11-05/2025-03-26/2025-06-18), server/discover alias; tools scan_project/explain_rule/check_compatibility/get_lineage/diagnose_log/diff_findings; resources forge-doctor-data://rules/ID + forge-doctor-data://knowledge/D/N
- lsp: optional [lsp] extra (pygls); workspace-root scans, unsaved-buffer overlay, debounced didChange, empty-publish clearing; source=forge-doctor-data, code=check id
- integrations: OpenLineage-shaped lineage export, CycloneDX 1.5 sbom, DOT/Mermaid graph renderers

## §V invariants
- V1 checks return CheckResult; never print/raise-for-user; never touch Rich/Typer
- V2 no code execution of analyzed project (no import, no subprocess of target code)
- V3 no secret material in any output field (values of .env/credentials never read into results)
- V4 traversal skips .git/.venv/node_modules/dist/build/__pycache__/*_cache by default
- V5 unknown check failure → internal ERROR result, not crash; --verbose shows traceback
- V6 checks carry stable IDs + file:line where applicable
- V7 warnings do NOT set exit 1 (only errors or --fail-on warning)
- V8 zero-config works: `scan` on empty/random dir produces valid output
- V9 offline: no network calls in any check
- V10 pyproject parsed once, cached in context; files listed once
- V11 domain checks consume a semantic model (analyzer → facts → checks);
  no per-check ad-hoc parsing/regex in domains that have a model

## §T tasks
id|status|task|cites
---|---|---|---
T1|x|scaffold pyproject+layout+lint/type config+gitignore+.editorconfig|C,I
T2|x|core models+context+registry+runner+config+traversal + tests|V1,V5,V8,V10
T3|x|renderers console/json/summary + cli + exit codes + tests|I,V7
T4|x|checks repository+python_env+git + tests + sample_projects|V1-V4,V6
T5|x|checks dependencies/spark-ast/aws + tests|V2,V3,V6,V9
T6|x|plugins discovery + `plugins` cmd + entry-point PoC test|I.plugins
T7|x|docs: README/CONTRIBUTING/CHANGELOG/LICENSE/checks.md/architecture|—
T8|x|CI ci.yml + release.yml (manual)|—
T9|x|verify: ruff+mypy+pytest+poetry build+pipx install+real runs|all
T10|x|v0.2: explain cmd + why/when_ok/fix on all checks|I
T11|x|v0.2: docker+ci+glue checks + tests + fixtures|V1-V4,V6,V9
T12|x|v0.2: example plugin + docs/plugins.md|I.plugins
T13|x|v0.3: baseline save/load/diff + is_new + exit-code-on-new-only|I,V7
T14|x|v0.3: console redesign (panels/tables/NEW markers/no-color) + html renderer|B1,B11
T15|x|v0.3: init scaffold + info stats + watch loop + --output + completion|V8
T16|x|v0.4: finding model v2 (confidence/fingerprint/tags/docs_uri/evidence/source/fixable/loc range) + json/sarif renderers + agent bundle|I
T17|x|v0.4: knowledge packs + compat engine + `compatibility` cmd + Glue 6.0|I
T18|x|v0.4: diff (reports+refs+base...head) + new-only + profiles + files filter + no-plugins + allowlist + workspace|I
T19|x|v0.4: spark AST v2 (aliases/df tracking/chained receivers/same-var unpersist) + SPARK008-011 + CI002 sha/tag/floating|V6
T20|x|v0.4: action.yml + .pre-commit-hooks.yaml + release OIDC/attestations + explain --json|—
T21|x|v0.7: fingerprint v3 + contract v3 + single-scan --emit + action.yml one-scan + jsonl|I
T22|x|v0.7: plugin identity + SDK v2 + plugins list/validate/doctor|I
T23|x|v0.7: policy-as-code + expiring suppressions + suppressions audit cmd|I
T24|x|v0.7: semantic index (one parse/file, cross-file producers) + spark/glue on it|V2
T25|x|v0.7: incremental cache + --cache/--no-cache + cache cmd + watchfiles/polling watch|—
T26|x|v0.7: trace + diagnose + error packs (spark/glue/iceberg/lakeformation/databricks/python)|V9
T27|x|v0.7: spark runtime (eventlog/plan/logs) — no pyspark dep|V2,V9
T28|x|v0.7: static lineage + schema diff + IaC checks (hcl-lite + CFN) + migrate glue|V2,V9
T29|x|v0.7: workspace scan/diff + knowledge provenance + sbom + mcp + lsp + graph + doctor|I
T30|x|v0.7: cli modularization (cli/ package, forge_doctor_data.cli:app unchanged) + runner dedupe + --stats|—
T31|x|v0.7: ci.yml smoke matrix (win/mac/ubu) + dogfood job + poetry>=2.2 + extras|—
T32|x|v0.8: [sql] extra (sqlglot) + SqlIndex (.sql files + call literals) + SQL000-003|I,V2,V9,V11
T33|x|v0.8: IcebergProjectModel + ICE000-013 + `iceberg` cmd group + iceberg packs|V11,I
T34|x|v0.8: ControlMModel (JSON defs + ctm CLI/API refs) + CTM000-070 + `controlm inspect` + controlm packs|V3,V9,V11,I
T35|x|v0.8: AirflowProjectModel (AST per airflow-flagged file) + AIR000-130 + `airflow inspect` + airflow packs|V2,V9,V11,I
T36|x|v0.8: TerraformProjectModel (hcl_lite block scan + ref graph) + TF000-130 + `terraform inspect` + terraform packs|I,V9,V11
T37|x|v0.8: ParquetProjectModel (code evidence + on-disk stats) + PARQ000-042 + `parquet inspect` + format pack|V9,V11
T38|x|v0.8: StepFunctionsModel (ASL/TF/CFN definitions) + SFN000-020 + `stepfunctions inspect` + integrations pack|V9,V11,I
T39|x|v0.8: StreamingProjectModel (stream-var lineage grouping) + STREAM001-070 + `streaming inspect` + stateful ops pack|V2,V9,V11
T40|x|v0.8: EvidenceKind (static/config/observed_metadata/runtime/derived) on CheckResult + category tagging + json/explain surfacing|I
T41|x|v0.8: DataPlatformGraph core (EntityKind/RelKind, canonical ids, evidence-tagged edges, reachable, to_dict)|—
T42|x|v0.8: platform graph population - 7 domain adapters + `platform graph`/`platform blast-radius` CLI; deterministic joins only|I,V9
T43|x|v0.8: graph identity hardening - catalog-aware table resolution, stream endpoint identifiers, semantic blast-radius directions|I,V9

## §B bugs
id|date|cause|fix
---|---|---|---
B1|2026-09-28|Windows cp1252 console can't encode ✓ℹ⚠✗ and →; Rich UnicodeEncodeError|encoding-aware icon/arrow fallback in output/console.py
B2|2026-09-28|pytest tmp_path outside workspace denied (WinError 5)|--basetemp=.pytest_tmp in addopts
B3|2026-09-28|5 strict-mypy errors in contract files|narrowed Any returns; typed visit_AsyncFor; annotated cli factory
B4|2026-09-28|DEP006: `poetry --version` subprocess fails inside pipx venv|kept INFO with honest message; likely slow-start timeout — revisit if reported
B5|2026-09-29|git ls-files emits repo-root-relative paths; scanning a repo subdirectory reported wrong file paths|strip `rev-parse --show-prefix` in context.git
B6|2026-09-29|subprocess text=True used locale codec; non-ASCII git ls-files output crashed all GIT checks|errors="replace" on subprocess probes
B7|2026-09-29|file field rendered OS separators (src\x.py on Windows) breaking JSON contract determinism|as_posix() in both renderers
B8|2026-09-29|DEP005 ran `poetry check --lock` on non-Poetry projects with stray poetry.lock|gate on is_poetry_managed
B9|2026-09-29|--format invalid discovered after the scan ran|validate before building the registry
B10|2026-09-29|console repeated title as location (REP001 "pyproject.toml pyproject.toml")|skip location when file.name == title
B11|2026-09-29|baseline line in summary panel printed literal [magenta] markup — Text.append does not parse markup|Text.from_markup for the baseline line
B12|2026-09-29|export_html with custom code_format returns a bare <pre> fragment, not a document|use default export_html format + regex title replace
B13|2026-09-29|--output ignored for json/sarif/agent formats (payload always went to stdout)|write rendered payload to --output for all machine formats
B14|2026-09-29|analyze_project called analyzer.visit() directly so df_names was never populated — chained-call receivers never reached HIGH confidence|expose dataframe_names(); set analyzer.df_names before visit
B15|2026-09-29|making tags/confidence/etc required Protocol members would break third-party plugin checks at runtime-check|keep v2 attrs optional; consumers use getattr defaults
B16|2026-09-29|CI002 treated @v4 tags as fully pinned though text said SHA is safer|grade refs: SHA quiet, tag INFO, floating/no-@ WARNING; security profile escalates
