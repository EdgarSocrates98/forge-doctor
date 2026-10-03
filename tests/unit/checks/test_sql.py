"""Unit tests for the SQL### checks ([sql] extra)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("sqlglot", reason="requires the [sql] extra")

from forge_doctor_data.checks.sql import (
    CHECKS,
    CartesianJoin,
    NonSargablePredicate,
    SelectStar,
    SqlSurface,
)
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity


def make_context(tmp_path: Path, files: dict[str, str]) -> ProjectContext:
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return ProjectContext(root=tmp_path)


DIRTY_SQL = "SELECT * FROM a CROSS JOIN b WHERE lower(name) = 'x';\n"
CLEAN_SQL = "SELECT a.id, b.name FROM a JOIN b ON a.id = b.id WHERE a.id > 10;\n"


def test_select_star_finding(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": DIRTY_SQL})
    results = SelectStar().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert results[0].file == Path("q.sql")
    assert results[0].line == 1
    assert results[0].check_id == "SQL001"


def test_cross_join_finding(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": DIRTY_SQL})
    results = CartesianJoin().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING
    assert "CROSS JOIN" in results[0].message


def test_implicit_join_message(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": "SELECT x FROM c, d;\n"})
    results = CartesianJoin().run(ctx)
    assert len(results) == 1
    assert "comma join" in results[0].message


def test_non_sargable_finding(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": DIRTY_SQL})
    results = NonSargablePredicate().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.WARNING


def test_call_site_attribution(tmp_path: Path) -> None:
    ctx = make_context(
        tmp_path,
        {
            "job.py": 'spark.sql("SELECT * FROM raw.events")\n',
        },
    )
    results = SelectStar().run(ctx)
    assert len(results) == 1
    assert results[0].file == Path("job.py")
    assert results[0].line == 1


def test_clean_sql_no_warnings(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": CLEAN_SQL})
    assert SelectStar().run(ctx) == []
    assert CartesianJoin().run(ctx) == []
    assert NonSargablePredicate().run(ctx) == []


def test_surface_anchor_counts(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": CLEAN_SQL, "bad.sql": "SELECT FROM WHERE;\n"})
    results = SqlSurface().run(ctx)
    assert len(results) == 1
    assert results[0].severity == Severity.INFO
    assert "1 SQL statements" in results[0].message
    assert "1 skipped" in results[0].message


def test_surface_no_sql(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"job.py": "x = 1\n"})
    results = SqlSurface().run(ctx)
    assert results[0].severity == Severity.PASS


def test_all_checks_run(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, {"q.sql": DIRTY_SQL})
    for check in CHECKS:
        results = check.run(ctx)
        assert all(r.check_id == check.id for r in results)


def test_degradation_without_sqlglot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without sqlglot the SQL category simply isn't registered."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    from forge_doctor_data.checks import builtin_checks

    assert [c.id for c in builtin_checks() if c.id.startswith("SQL")] == []


def test_explain_hint_without_sqlglot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from forge_doctor_data.cli import app

    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    result = CliRunner().invoke(app, ["explain", "SQL001"])
    assert result.exit_code != 0
    assert "sql extra" in result.output
