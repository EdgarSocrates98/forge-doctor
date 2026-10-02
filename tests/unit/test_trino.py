"""Trino adapter: model, checks, CLI (spec 218)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor.analyzers.trino_model import trino_model
from forge_doctor.checks.trino import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Confidence, Severity

runner = CliRunner()
needs_sqlglot = pytest.mark.skipif(not SQLGLOT_AVAILABLE, reason="sqlglot not installed")


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _write_catalogs(tmp_path: Path) -> Path:
    cat = tmp_path / "etc" / "catalog"
    cat.mkdir(parents=True)
    return cat


def _findings(tmp_path: Path) -> dict[str, str]:
    ctx = _ctx(tmp_path)
    return {f.check_id: f.message for c in CHECKS for f in c.run(ctx)}


# ---------------------------------------------------------------------------
# Model


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "app.properties").write_text("server.port=8080\n", encoding="utf-8")
    assert not trino_model(_ctx(tmp_path)).has_evidence


def test_catalog_parsing(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "hive.properties").write_text(
        "# comment\nconnector.name=hive\nhive.metastore.uri=thrift://ms:9083\n! another comment\n",
        encoding="utf-8",
    )
    (cat / "tpch.properties").write_text("connector.name=tpch\n", encoding="utf-8")
    model = trino_model(_ctx(tmp_path))
    assert model.has_evidence
    assert model.catalog_names() == {"hive", "tpch"}
    assert model.connector("hive") == "hive"
    assert model.connector("tpch") == "tpch"
    assert model.connector("missing") == ""


def test_catalog_without_connector_ignored(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "notes.properties").write_text("purpose=scratch\n", encoding="utf-8")
    model = trino_model(_ctx(tmp_path))
    assert not model.has_evidence
    assert model.catalogs == []


def test_coordinator_config(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=true\ndiscovery.uri=http://c:8080\nquery.max-memory=50GB\n",
        encoding="utf-8",
    )
    model = trino_model(_ctx(tmp_path))
    assert model.has_evidence
    assert model.is_coordinator
    assert model.coordinator_props["query.max-memory"] == "50GB"
    assert not model.has_spill_config()
    assert not model.has_resource_groups()


def test_worker_config_split(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=false\nspiller-spill-path=/tmp/spill\n", encoding="utf-8"
    )
    model = trino_model(_ctx(tmp_path))
    assert not model.is_coordinator
    assert model.has_spill_config()  # worker props count


def test_node_and_jvm(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "node.properties").write_text(
        "node.environment=production\n", encoding="utf-8"
    )
    (tmp_path / "etc" / "jvm.config").write_text("-server\n-Xmx16G\n# comment\n", encoding="utf-8")
    model = trino_model(_ctx(tmp_path))
    assert model.node_props["node.environment"] == "production"
    assert model.jvm_flags == ("-server", "-Xmx16G")


def test_stray_node_properties_not_claimed(tmp_path: Path) -> None:
    """node.properties outside the etc/ tree isn't Trino evidence."""
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "node.properties").write_text("x=y\n", encoding="utf-8")
    assert not trino_model(_ctx(tmp_path)).has_evidence


def test_resource_groups_file(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "resource-groups.json").write_text(
        json.dumps({"rootGroups": []}), encoding="utf-8"
    )
    assert trino_model(_ctx(tmp_path)).has_resource_groups()


def test_event_listener(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "event-listener.properties").write_text(
        "event-listener.name=audit\n", encoding="utf-8"
    )
    assert trino_model(_ctx(tmp_path)).event_listener_file is not None


@needs_sqlglot
def test_three_part_refs(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "q.sql").write_text(
        "SELECT * FROM ice.marts.fact f JOIN wh.dw.dim d ON f.id = d.id;\n",
        encoding="utf-8",
    )
    model = trino_model(_ctx(tmp_path))
    catalogs = {r.catalog for r in model.refs}
    assert {"ice", "wh"} <= catalogs


def test_observed_cluster_export(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    export = tmp_path / "trino" / "cluster.json"
    export.parent.mkdir(parents=True)
    export.write_text(
        json.dumps({"coordinator": True, "nodeVersion": "434", "environment": "prod"}),
        encoding="utf-8",
    )
    (tmp_path / "trino" / "noise.json").write_text('{"unrelated": {"deep": 1}}')
    model = trino_model(_ctx(tmp_path))
    assert len(model.observed) == 1
    assert model.observed[0].get("nodeVersion") == "434"
    assert "trino/noise.json" in model.unparsed


# ---------------------------------------------------------------------------
# Checks


def test_trino000_census(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=true\ndiscovery.uri=http://c:1\n", encoding="utf-8"
    )
    found = _findings(tmp_path)
    assert "TRINO000" in found
    assert "iceberg" in found["TRINO000"]


def test_trino000_silent_on_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "TRINO000"]
    assert res and res[0].severity == Severity.PASS


def test_trino001_hive_no_metastore(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "hive.properties").write_text("connector.name=hive\n", encoding="utf-8")
    assert "TRINO001" in _findings(tmp_path)


def test_trino001_hive_with_metastore_ok(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "hive.properties").write_text(
        "connector.name=hive\nhive.metastore.uri=thrift://ms:9083\n", encoding="utf-8"
    )
    assert "TRINO001" not in _findings(tmp_path)


@needs_sqlglot
def test_trino002_no_spill_with_writes(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=true\ndiscovery.uri=http://c:1\n", encoding="utf-8"
    )
    (tmp_path / "etl.sql").write_text(
        "INSERT INTO ice.marts.fact SELECT * FROM ice.staging.raw;\n", encoding="utf-8"
    )
    assert "TRINO002" in _findings(tmp_path)


def test_trino002_spill_configured_ok(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=true\ndiscovery.uri=http://c:1\nspiller-spill-path=/data\n",
        encoding="utf-8",
    )
    assert "TRINO002" not in _findings(tmp_path)


def test_trino003_test_connector_in_prod(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (cat / "tpch.properties").write_text("connector.name=tpch\n", encoding="utf-8")
    found = _findings(tmp_path)
    assert "TRINO003" in found
    assert "tpch" in found["TRINO003"]


def test_trino003_all_test_connectors_quiet(tmp_path: Path) -> None:
    """A pure-benchmark cluster (only tpch) isn't a prod deployment."""
    cat = _write_catalogs(tmp_path)
    (cat / "tpch.properties").write_text("connector.name=tpch\n", encoding="utf-8")
    assert "TRINO003" not in _findings(tmp_path)


def test_trino004_multi_catalog_no_rg(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "a.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (cat / "b.properties").write_text("connector.name=jdbc\n", encoding="utf-8")
    assert "TRINO004" in _findings(tmp_path)


def test_trino004_with_rg_ok(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "a.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (cat / "b.properties").write_text("connector.name=jdbc\n", encoding="utf-8")
    (tmp_path / "etc" / "resource-groups.json").write_text("{}", encoding="utf-8")
    assert "TRINO004" not in _findings(tmp_path)


def test_trino004_single_catalog_quiet(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "a.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    assert "TRINO004" not in _findings(tmp_path)


@needs_sqlglot
def test_trino005_unknown_catalog_ref(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "q.sql").write_text(
        "SELECT * FROM ice.marts.fact f JOIN ghost.dw.dim d ON f.id = d.id;\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    res = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "TRINO005"]
    assert res
    assert res[0].confidence == Confidence.MEDIUM
    assert "ghost" in res[0].message


@needs_sqlglot
def test_trino005_known_catalogs_quiet(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "ice.properties").write_text("connector.name=iceberg\n", encoding="utf-8")
    (tmp_path / "q.sql").write_text("SELECT * FROM ice.marts.fact;\n", encoding="utf-8")
    assert "TRINO005" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# CLI


def test_trino_inspect_cli(tmp_path: Path) -> None:
    cat = _write_catalogs(tmp_path)
    (cat / "hive.properties").write_text("connector.name=hive\n", encoding="utf-8")
    (tmp_path / "etc" / "config.properties").write_text(
        "coordinator=true\ndiscovery.uri=http://c:1\n", encoding="utf-8"
    )
    res = runner.invoke(app, ["trino", "inspect", str(tmp_path)])
    assert res.exit_code == 0
    assert "hive" in res.output


def test_trino_inspect_empty(tmp_path: Path) -> None:
    res = runner.invoke(app, ["trino", "inspect", str(tmp_path)])
    assert res.exit_code == 0
