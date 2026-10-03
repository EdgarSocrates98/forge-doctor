# Run: 2026-09-30 — SQL first-class (spec 112, data-intelligence cycle kickoff)

Driver: `prompt_evo_novas_evolucoes.md` — post-hardening review. Engine declared
**stable**; focus moves to Data Engineering Intelligence Packs with the new
principle `analyzers → semantic facts → domain models → findings`.

## Specs

| spec | state | result |
|---|---|---|
| 112-sql-semantic-model | active | built, awaiting review |
| 113-iceberg-doctor | inbox | ready (queued) |
| 114-lakeformation-doctor | inbox | ready (queued) |
| 115-spark-perf-v2 | inbox | ready (queued) |

## What was built (112)

- `[sql]` optional extra: `sqlglot>=26,<29` (installed 27.29 satisfies),
  declared in `pyproject` extras + dev group; `sqlglot*` added to the mypy
  `follow_imports=skip` overrides; `poetry lock` refreshed.
- `analyzers/sql_ast.py` — `SqlIndex`/`SqlStatement`:
  `.sql` files tokenized once and split on `SEMICOLON` tokens (exact line
  numbers, per-statement error isolation — a bad statement can't swallow
  good ones beyond the next `;`); `*.sql(...)` call sites reuse the
  `CallSite.args` literals already captured by the semantic index — no
  re-parse of Python. Facts: tables_read/written (CTE aliases excluded,
  Insert/Create/Merge targets excluded from reads), wildcard (Star under
  Select, excluding `COUNT(*)` via AggFunc), cross/implicit joins
  (comma joins arrive as bare `kind=CROSS` joins; explicit `CROSS JOIN`
  carries extra args — discriminator tested), non-sargable
  (func-wrapped column vs column-free side in WHERE comparisons).
  Dialect: spark first, generic fallback for `.sql` files; spark for
  call-site literals.
- `checks/sql.py` — SQL000 surface anchor (INFO, reports unparsed count),
  SQL001 `SELECT *`, SQL002 cartesian/implicit joins, SQL003 non-sargable.
- Conditional registration: `builtin_checks()` appends the sql module only
  when `find_spec("sqlglot")` succeeds — zero behavior change on base
  installs. `explain SQL###` without the extra hints at `forge-doctor-data[sql]`.
- Backprop: SPEC.md gains **V11** (domain checks consume semantic models —
  no per-check ad-hoc parsing); T32 recorded; roadmap Next reordered per the
  reviewer's priority and marked SQL shipped.

## Verification

- `pytest tests/unit/test_sql_ast.py tests/unit/checks/test_sql.py` — 21 passed
- `pytest -x -q` — 430 passed
- `ruff check src tests` — clean
- `ruff format --check` — clean
- `mypy src` — 68 files, no issues
- `poetry check --lock` — OK after `poetry lock`
- Manual e2e: `scan --check sql` on a fixture with `q.sql` + `spark.sql("...")`
  attributes the literal to `job.py:1` with evidence lines.

## Notes / parked

- **Parked by reviewer (not built):** splitting `_export_signature` into
  module-hash vs export-hash — perf optimization for huge monorepos only;
  and `plugins.mode = "explicit"` — still an open product decision.
- sqlglot falls back to `exp.Command` for unrecognized text — genuinely
  unparseable input is rarer than expected; `SELECT FROM WHERE` used in tests.
- SQL → lineage/graph edges deliberately NOT wired (follow-up spec).
- Next in the cycle: spec 113 (Iceberg) is the highest-leverage queued spec.
