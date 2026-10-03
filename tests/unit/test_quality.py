"""Data quality evidence: model, DQ checks, CLI (spec 222)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor_data.analyzers.quality_model import quality_model
from forge_doctor_data.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor_data.checks.quality import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _findings(tmp_path: Path) -> dict[str, list[str]]:
    ctx = _ctx(tmp_path)
    out: dict[str, list[str]] = {}
    for c in CHECKS:
        for f in c.run(ctx):
            out.setdefault(f.check_id, []).append(f.message)
    return out


_GX_SUITE = {
    "expectation_suite_name": "orders.warning",
    "meta": {"data_asset_name": "dw.orders"},
    "expectations": [
        {"expectation_type": "expect_table_row_count_to_be_between"},
        {
            "expectation_type": "expect_column_values_to_not_be_null",
            "kwargs": {"column": "order_id"},
        },
    ],
}

_SODA = """checks for orders:
  - row_count > 0
  - missing_count(email) = 0
"""

_DEEQU = """from pydeequ.checks import Check, CheckLevel
from pydeequ.verifications import VerificationSuite

check = Check(CheckLevel.Error, "orders check")
VerificationSuite().onData(df).addCheck(
    check.isComplete("order_id").isUnique("order_id")
).run()
"""


def _gx(tmp_path: Path, name: str = "suite.json", doc: dict | None = None) -> None:
    d = tmp_path / "great_expectations" / "expectations"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(doc or _GX_SUITE), encoding="utf-8")


def _ddl(tmp_path: Path, ddl: str) -> None:
    (tmp_path / "ddl.sql").write_text(ddl, encoding="utf-8")


# ---------------------------------------------------------------------------
# Model


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text('{"checks": {}}', encoding="utf-8")
    assert not quality_model(_ctx(tmp_path)).has_evidence


def test_gx_suite(tmp_path: Path) -> None:
    _gx(tmp_path)
    model = quality_model(_ctx(tmp_path))
    assert len(model.suites) == 1
    s = model.suites[0]
    assert s.engine == "great_expectations"
    assert s.name == "orders.warning"
    assert s.table == "orders"
    assert s.columns == ("order_id",)
    assert not s.wired


def test_gx_checkpoint_wires(tmp_path: Path) -> None:
    _gx(tmp_path)
    cp = tmp_path / "great_expectations" / "checkpoints"
    cp.mkdir(parents=True, exist_ok=True)
    (cp / "cp.yml").write_text(
        "name: cp\nvalidations:\n  - expectation_suite_name: orders.warning\n",
        encoding="utf-8",
    )
    model = quality_model(_ctx(tmp_path))
    assert model.suites[0].wired
    assert model.gates[0].suite_refs == ("orders.warning",)


def test_gx_observed_run(tmp_path: Path) -> None:
    _gx(tmp_path)
    val = tmp_path / "uncommitted" / "validations"
    val.mkdir(parents=True, exist_ok=True)
    (val / "r.json").write_text(
        json.dumps({"expectation_suite_name": "orders.warning", "success": True}),
        encoding="utf-8",
    )
    model = quality_model(_ctx(tmp_path))
    assert model.observed_runs
    assert model.suites[0].wired


def test_soda_checks(tmp_path: Path) -> None:
    (tmp_path / "soda").mkdir()
    (tmp_path / "soda" / "checks.yml").write_text(_SODA, encoding="utf-8")
    model = quality_model(_ctx(tmp_path))
    s = model.suites[0]
    assert s.engine == "soda"
    assert s.table == "orders"
    assert "email" in s.columns
    assert len(s.expectations) == 2


def test_soda_adversarial_checks_key(tmp_path: Path) -> None:
    """A CI `checks:` key is not SodaCL."""
    (tmp_path / "checks.yml").write_text("checks:\n  strict: true\n", encoding="utf-8")
    assert not quality_model(_ctx(tmp_path)).has_evidence


def test_deequ_suite(tmp_path: Path) -> None:
    (tmp_path / "dq.py").write_text(_DEEQU, encoding="utf-8")
    model = quality_model(_ctx(tmp_path))
    assert len(model.suites) == 1
    s = model.suites[0]
    assert s.engine == "deequ"
    assert s.wired  # code-bound
    assert "order_id" in s.columns


def test_dbt_tests_reused(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: p\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "orders.sql").write_text("select 1", encoding="utf-8")
    (models / "schema.yml").write_text(
        "version: 2\nmodels:\n  - name: orders\n    tests: [unique]\n",
        encoding="utf-8",
    )
    model = quality_model(_ctx(tmp_path))
    dbt_suites = [s for s in model.suites if s.engine == "dbt"]
    assert dbt_suites and dbt_suites[0].table == "orders"


def test_dbt_wired_by_invocation(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: p\n", encoding="utf-8")
    models = tmp_path / "models"
    models.mkdir()
    (models / "m.sql").write_text("select 1", encoding="utf-8")
    (models / "schema.yml").write_text(
        "version: 2\nmodels:\n  - name: m\n    tests: [unique]\n",
        encoding="utf-8",
    )
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("steps:\n  - run: dbt test\n", encoding="utf-8")
    model = quality_model(_ctx(tmp_path))
    assert all(s.wired for s in model.suites)


# ---------------------------------------------------------------------------
# Checks


def test_dq000_anchor(tmp_path: Path) -> None:
    _gx(tmp_path)
    assert "DQ000" in _findings(tmp_path)


@needs_sqlglot
def test_dq001_prod_uncovered(tmp_path: Path) -> None:
    _gx(tmp_path)  # covers orders
    _ddl(tmp_path, "CREATE TABLE orders (order_id INT);\nCREATE TABLE prod_x (id INT);\n")
    out = _findings(tmp_path)
    assert "DQ001" in out
    assert any("prod_x" in m for m in out["DQ001"])


@needs_sqlglot
def test_dq001_silent_without_practice(tmp_path: Path) -> None:
    _ddl(tmp_path, "CREATE TABLE prod_x (id INT);\n")
    assert "DQ001" not in _findings(tmp_path)


def test_dq002_unwired(tmp_path: Path) -> None:
    _gx(tmp_path)
    assert "DQ002" in _findings(tmp_path)


@needs_sqlglot
def test_dq003_stale_suite(tmp_path: Path) -> None:
    _gx(tmp_path, doc={**_GX_SUITE, "meta": {"data_asset_name": "ghost_t"}})
    _ddl(tmp_path, "CREATE TABLE orders (id INT);\n")
    out = _findings(tmp_path)
    assert "DQ003" in out
    assert any("ghost_t" in m for m in out["DQ003"])


@needs_sqlglot
def test_dq004_dropped_column(tmp_path: Path) -> None:
    (tmp_path / "datacontract.yml").write_text(
        "dataContractSpecification: 1.1.0\nid: c\ninfo:\n  title: t\n"
        "schema:\n  - name: orders\n    fields:\n      order_id:\n        type: bigint\n",
        encoding="utf-8",
    )
    _ddl(tmp_path, "CREATE TABLE orders (order_id BIGINT);\n")
    _gx(
        tmp_path,
        doc={
            **_GX_SUITE,
            "expectations": [
                {
                    "expectation_type": "expect_column_values_to_be_between",
                    "kwargs": {"column": "dropped_col"},
                }
            ],
        },
    )
    out = _findings(tmp_path)
    assert "DQ004" in out
    assert any("dropped_col" in m for m in out["DQ004"])


def test_dq004_silent_without_schema(tmp_path: Path) -> None:
    """No detected fields -> column drift is unproven, stay silent."""
    _gx(tmp_path)
    assert "DQ004" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# CLI


def test_quality_inspect(tmp_path: Path) -> None:
    _gx(tmp_path)
    result = runner.invoke(app, ["quality", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "orders.warning" in result.output
    assert "unwired" in result.output


def test_quality_inspect_empty(tmp_path: Path) -> None:
    result = runner.invoke(app, ["quality", "inspect", str(tmp_path)])
    assert result.exit_code == 0
    assert "no data-quality evidence" in result.output
