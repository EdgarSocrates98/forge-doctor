"""Unit tests for the sqlglot-backed SQL statement index ([sql] extra)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sqlglot", reason="requires the [sql] extra")

from forge_doctor.analyzers.sql_ast import analyze_sql
from forge_doctor.core.context import ProjectContext


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


def test_sql_file_statements(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"queries/etl.sql": "SELECT * FROM bronze.orders;\n\nSELECT a, b FROM dim.customers;\n"},
    )
    index = analyze_sql(ctx)
    assert len(index.statements) == 2
    first, second = index.statements
    assert first.line == 1
    assert second.line == 3
    assert first.source == "file"
    assert first.wildcard and not second.wildcard
    assert first.tables_read == ("bronze.orders",)
    assert second.tables_read == ("dim.customers",)


def test_call_site_literal(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": "from pyspark.sql import SparkSession\n\n"
            "spark = SparkSession.builder.getOrCreate()\n\n"
            'spark.sql("SELECT * FROM raw.events")\n'
        },
    )
    index = analyze_sql(ctx)
    assert len(index.statements) == 1
    stmt = index.statements[0]
    assert stmt.source == "call"
    assert stmt.line == 5
    assert stmt.dialect == "spark"
    assert stmt.wildcard
    assert stmt.tables_read == ("raw.events",)


def test_cross_and_implicit_joins(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.sql": "SELECT * FROM a CROSS JOIN b;\n"
            "SELECT x FROM c, d;\n"
            "SELECT y FROM e JOIN f ON e.id = f.id;\n"
        },
    )
    stmts = analyze_sql(ctx).statements
    assert [s.cross_join for s in stmts] == [True, True, False]
    assert [s.implicit_join for s in stmts] == [False, True, False]


def test_non_sargable(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "q.sql": "SELECT a FROM t WHERE date_trunc('day', ts) = '2024-01-01';\n"
            "SELECT a FROM t WHERE ts >= '2024-01-01';\n"
        },
    )
    stmts = analyze_sql(ctx).statements
    assert [s.non_sargable for s in stmts] == [True, False]


def test_cte_names_not_read_tables(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "WITH x AS (SELECT * FROM bronze.orders) SELECT id FROM x;\n"},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert stmt.tables_read == ("bronze.orders",)


def test_count_star_not_wildcard(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": "SELECT COUNT(*) FROM t;\n"})
    (stmt,) = analyze_sql(ctx).statements
    assert not stmt.wildcard


def test_written_tables(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "INSERT INTO mart.daily SELECT a, b FROM src.s;\n"},
    )
    (stmt,) = analyze_sql(ctx).statements
    assert stmt.tables_written == ("mart.daily",)
    assert stmt.tables_read == ("src.s",)


def test_unparseable_counted_no_crash(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {"q.sql": "SELECT a FROM t;\nSELECT FROM WHERE;\nSELECT b FROM u;\n"},
    )
    index = analyze_sql(ctx)
    assert index.unparsed == 1
    assert len(index.statements) == 2


def test_deterministic_order(tmp_path: Path) -> None:
    files = {"b.sql": "SELECT * FROM b;\n", "a.sql": "SELECT x FROM a;\n", "j.py": "x=1\n"}
    first = analyze_sql(make_context(tmp_path / "p1", files))
    second = analyze_sql(make_context(tmp_path / "p2", files))
    assert [(s.file.name, s.line) for s in first.statements] == [
        (s.file.name, s.line) for s in second.statements
    ]
    assert [s.file.name for s in first.statements] == ["a.sql", "b.sql"]


def test_no_sql_project(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "x = 1\n"})
    index = analyze_sql(ctx)
    assert index.statements == []
    assert index.unparsed == 0
