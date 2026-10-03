---
id: 112-sql-semantic-model
title: SQL first-class — sqlglot extra, SqlIndex model, SQL000-003 checks
agent: devin
risk: medium
grill: completed
verification:
  - python -m pytest tests/unit/test_sql_ast.py tests/unit/checks/test_sql.py -q
  - python -m pytest -x -q
  - python -m mypy src
  - python -m ruff check src tests
---

# Context
Review `prompt_evo_novas_evolucoes.md` froze the engine ("ENGINE STABLE") and
reordered the roadmap: Data Engineering Intelligence Packs, **SQL first** —
SQL connects Spark SQL, Iceberg, Athena, Databricks, dbt. The reviewer
prescribed the architecture: parsers → semantic facts → domain models →
findings (never per-check regex). SQL analysis must follow the existing
analyzer/index pattern, not ad-hoc scanning.

# Acceptance Criteria
- New optional extra `sql = ["sqlglot>=26,<29"]` in pyproject + sqlglot added
  to the mypy `follow_imports=skip` overrides (same pattern as pygls/yaml) +
  added to dev dependency-group so tests exercise it.
- New `src/forge_doctor_data/analyzers/sql_ast.py` producing `SqlIndex`:
  - `SqlStatement` records: file, line, source (`"file"` | `"call"`), dialect,
    `tables_read`, `tables_written`, `wildcard`, `cross_join`,
    `implicit_join`, `non_sargable` (bool flags), sorted deterministic order.
  - Sources: every `*.sql` file under traversal, AND string-literal first
    args of `.sql(`/`spark.sql(` call sites already captured in
    `ModuleIndex.calls` (`CallSite.args`) — reuse the index, no re-parse of
    Python. Dialect `spark` for call-site literals; `.sql` files parsed with
    generic dialect, dialect recorded on the record.
  - `sqlglot` parse failures never crash: statement skipped, counted in
    `SqlIndex.unparsed`.
  - Analysis memoized on the context (same pattern as
    `analyze_project`/`_forge_doctor_data_spark_buckets`).
- New `src/forge_doctor_data/checks/sql.py`, `category = "sql"`, `CHECKS` list:
  - `SQL000` SQL surface (INFO anchor: statements analyzed + unparsed count).
  - `SQL001` `SELECT *` wildcard projection (WARNING).
  - `SQL002` CROSS JOIN or implicit comma join `FROM a, b` (WARNING).
  - `SQL003` non-sargable predicate — column wrapped in a function on one
    side of a WHERE comparison (WARNING).
  - All with why/when_ok/fix; findings carry file + line.
- Conditional registration in `checks/__init__.py`: `sql` module appended to
  builtins only when `importlib.util.find_spec("sqlglot")` succeeds.
  `explain SQL001` without the extra prints a clear "requires the sql extra"
  hint instead of a bare unknown-id error (prefix `SQL` special-case in
  explain).
- `.sql` files need no traversal change if `iter_files` already yields them;
  verify they are not excluded by default.
- Docs: `docs/checks.md` gains the sql category rows; README check count and
  category list updated; CHANGELOG entry.
- Tests:
  - `.sql` file with `SELECT *` + `CROSS JOIN` → SQL001+SQL002 findings.
  - `spark.sql("SELECT * FROM t")` literal in a `.py` file → SQL001 finding
    attributed to the call-site file/line.
  - Clean SQL (explicit columns, equijoin) → no WARNING findings.
  - Unparseable SQL → SQL000 INFO reports `unparsed` count, no crash.
  - Degradation: monkeypatch `find_spec` to hide sqlglot → `builtin_checks()`
    contains no `SQL` ids, no error.
  - Deterministic: two scans produce identical finding order.

# Constraints
- sqlglot is OPTIONAL: the base install must scan a repo containing `.sql`
  files with zero behavior change when the extra is absent (no findings, no
  import errors).
- Never execute analyzed code; string literals only.
- Determinism: sort all statement/finding output by (file, line).
- Do NOT feed SQL tables into `lineage`/`graph` yet — that's the follow-up
  spec (kept out so this stays reviewable).

# Review Notes
- Follow-up (next specs): SQL tables → lineage/graph edges; Iceberg ICE###;
  Lake Formation pack; Spark perf v2 using propagated df_names.
- Parked by reviewer (do not build): split `_export_signature` into
  module-hash vs export-hash (perf optimization for huge monorepos only);
  `plugins.mode = "explicit"` remains an open product decision.
