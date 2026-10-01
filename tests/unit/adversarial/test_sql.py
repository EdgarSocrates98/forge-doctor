"""Adversarial coverage for the SQL model (spec 170).

Parser-version contract: sqlglot>=26,<29 tokenizes some identifiers as
reserved keywords in 28.x that were plain identifiers in 27.x. Known
version-sensitive identifiers: ``out`` (TokenType.OUT since 28.x).
Unquoted reserved-looking identifiers may therefore parse on one
supported version and fail on another - the contract is *graceful
degradation* (statement counted as unparsed), never an exception.
"""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.sql_ast import analyze_sql
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_reserved_identifier_never_raises(tmp_path: Path) -> None:
    """``out`` is reserved in sqlglot 28; parse must degrade, not crash."""
    ctx = make_context(
        tmp_path,
        {"q.sql": "INSERT INTO out.daily SELECT a, b FROM src.s;\n"},
    )
    idx = analyze_sql(ctx)
    # Either the identifier parses (older sqlglot) or it is counted
    # unparsed (28.x) - both are acceptable, a crash is not.
    assert len(idx.statements) + idx.unparsed >= 1


def test_quoted_reserved_identifier_parses(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": 'INSERT INTO "out".daily SELECT a FROM src.s;\n'},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert stmt.tables_written == ("out.daily",)


def test_comment_not_part_of_statement(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "-- SELECT * FROM fake_table\nSELECT a FROM real.t;\n"},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert stmt.tables_read == ("real.t",)


def test_truncated_statement_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": "SELECT * FROM\n"})
    idx = analyze_sql(ctx)
    assert idx.statements == []
    assert idx.unparsed >= 1


def test_garbage_file_graceful(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": "this is not sql at all !!!\n"})
    idx = analyze_sql(ctx)
    assert idx.unparsed >= 1


def test_aliased_tables_resolve_base_names(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "SELECT a FROM src.orders o JOIN cust.c c ON o.cid = c.id;\n"},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert set(stmt.tables_read) == {"src.orders", "cust.c"}


def test_cte_does_not_leak_into_tables_read(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "WITH x AS (SELECT a FROM src.t) INSERT INTO mart.d SELECT * FROM x;\n"},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert "src.t" in stmt.tables_read
    assert "x" not in stmt.tables_read
    assert stmt.tables_written == ("mart.d",)


def test_multi_statement_file_lines(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "SELECT 1 FROM a.t;\n\nSELECT 2 FROM b.u;\n"},
    )
    stmts = analyze_sql(ctx).statements
    assert len(stmts) == 2
    assert stmts[0].line < stmts[1].line


def test_empty_file(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": ""})
    idx = analyze_sql(ctx)
    assert idx.statements == []
    assert idx.unparsed == 0
