"""dbt adapter: model, checks, CLI, graph lineage (spec 216)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor_data.analyzers.dbt_model import dbt_model
from forge_doctor_data.checks.dbt import CHECKS
from forge_doctor_data.cli.app import app
from forge_doctor_data.core.context import ProjectContext
from forge_doctor_data.core.models import Severity

runner = CliRunner()


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


_PROJECT = """
name: analytics
profile: warehouse
model-paths: [models]
"""

_SCHEMA = """
version: 2

sources:
  - name: raw
    freshness:
      warn_after:
        count: 12
        period: hour
    tables:
      - name: orders
        description: Raw order stream.
  - name: unused_src
    tables:
      - name: archive

models:
  - name: stg_orders
    description: Staged orders.
    columns:
      - name: order_id
        tests:
          - unique
          - not_null
"""

_STG = """
{{ config(materialized='view') }}
select order_id from {{ source('raw', 'orders') }}
"""

_FACT = """
{{ config(materialized='incremental') }}
select order_id from {{ ref('stg_orders') }}
"""


def _project(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text(_PROJECT, encoding="utf-8")
    (tmp_path / "models" / "staging").mkdir(parents=True)
    (tmp_path / "models" / "marts").mkdir(parents=True)
    (tmp_path / "models" / "staging" / "stg_orders.sql").write_text(_STG, encoding="utf-8")
    (tmp_path / "models" / "marts" / "fact_orders.sql").write_text(_FACT, encoding="utf-8")
    (tmp_path / "models" / "schema.yml").write_text(_SCHEMA, encoding="utf-8")


def test_no_project_no_evidence(tmp_path: Path) -> None:
    """No dbt_project.yml -> empty model (adversarial)."""
    (tmp_path / "q.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (tmp_path / "s.yml").write_text("version: 2\nfoo: bar\n", encoding="utf-8")
    model = dbt_model(_ctx(tmp_path))
    assert not model.has_evidence
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx) if f.severity != Severity.PASS}
    assert not found


def test_project_and_models(tmp_path: Path) -> None:
    _project(tmp_path)
    model = dbt_model(_ctx(tmp_path))
    assert model.has_evidence
    assert model.project_name == "analytics"
    assert model.model_dirs == ("models",)
    by_name = {m.name: m for m in model.models}
    assert by_name["stg_orders"].materialized == "view"
    assert by_name["stg_orders"].sources_used == ("raw.orders",)
    assert by_name["stg_orders"].has_description
    assert set(by_name["stg_orders"].tests) == {"unique", "not_null"}
    assert by_name["fact_orders"].materialized == "incremental"
    assert by_name["fact_orders"].refs == ("stg_orders",)


def test_manifest_and_run_results_observed(tmp_path: Path) -> None:
    _project(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "manifest.json").write_text(
        json.dumps({"nodes": {"model.a.x": {}, "source.a.raw.o": {}}}), encoding="utf-8"
    )
    (target / "run_results.json").write_text(
        json.dumps(
            {"results": [{"unique_id": "model.a.x", "status": "success", "execution_time": 1.2}]}
        ),
        encoding="utf-8",
    )
    model = dbt_model(_ctx(tmp_path))
    assert model.manifest_nodes == 2
    assert model.run_results == [("model.a.x", "success", 1.2)]


def test_dbt001_untested_model(tmp_path: Path) -> None:
    _project(tmp_path)
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "DBT001"]
    assert found
    assert any("fact_orders" in f.message for f in found)
    assert not any("stg_orders" in f.message for f in found)


def test_dbt002_incremental_no_unique_key(tmp_path: Path) -> None:
    _project(tmp_path)
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DBT002" in found


def test_dbt002_incremental_with_key_quiet(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "models" / "marts" / "fact_orders.sql").write_text(
        "{{ config(materialized='incremental', unique_key='order_id') }}\n"
        "select order_id from {{ ref('stg_orders') }}\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DBT002" not in found


def test_dbt003_source_without_freshness(tmp_path: Path) -> None:
    _project(tmp_path)
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "DBT003"]
    assert found
    assert any("unused_src.archive" in f.message for f in found)
    assert not any("raw.orders" in f.message for f in found)


def test_dbt004_unused_source(tmp_path: Path) -> None:
    _project(tmp_path)
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "DBT004"]
    assert found
    assert any("unused_src.archive" in f.message for f in found)
    assert not any("raw.orders" in f.message for f in found)


def test_dbt005_low_doc_coverage(tmp_path: Path) -> None:
    _project(tmp_path)
    # Remove stg_orders description -> 0/2 described < 50%
    (tmp_path / "models" / "schema.yml").write_text(
        _SCHEMA.replace("    description: Staged orders.\n", ""), encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DBT005" in found


def test_dbt005_at_threshold_quiet(tmp_path: Path) -> None:
    _project(tmp_path)  # 1/2 described = 50% -> at threshold, no finding
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DBT005" not in found


def test_profiles_key_names_only(tmp_path: Path) -> None:
    """profiles.yml values never reach the model — key names only."""
    _project(tmp_path)
    (tmp_path / "profiles.yml").write_text(
        "warehouse:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: snowflake\n"
        "      account: acct123\n"
        "      user: svc_user\n"
        "      password: hunter2\n",
        encoding="utf-8",
    )
    model = dbt_model(_ctx(tmp_path))
    assert model.profile_names == ["warehouse"]
    assert set(model.profile_keys) >= {"type", "account", "user", "password"}
    blob = str(model)
    assert "hunter2" not in blob
    assert "svc_user" not in blob
    assert "acct123" not in blob


def test_graph_lineage(tmp_path: Path) -> None:
    """ref/source edges land in the platform graph."""
    from forge_doctor_data.analyzers.platform_graph_builder import build_platform_graph

    _project(tmp_path)
    g = build_platform_graph(_ctx(tmp_path))
    fact = g.entity("dbt_model:dbt:fact_orders")
    stg = g.entity("dbt_model:dbt:stg_orders")
    assert fact is not None and stg is not None
    out = {(r.dst, r.kind.value) for r in g.outbound(fact.id)}
    assert ("dbt_model:dbt:stg_orders", "READS_FROM") in out
    assert any(dst.endswith("fact_orders") and kind == "WRITES_TO" for dst, kind in out)
    stg_out = {(r.dst, r.kind.value) for r in g.outbound(stg.id)}
    assert any(dst.endswith("raw.orders") and kind == "READS_FROM" for dst, kind in stg_out)


def test_cli_inspect(tmp_path: Path) -> None:
    _project(tmp_path)
    result = runner.invoke(app, ["dbt", "inspect", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "dbt environment" in result.output
    assert "stg_orders" in result.output
    assert "unused_src.archive" in result.output
