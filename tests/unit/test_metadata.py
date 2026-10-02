"""Metadata governance: model, META checks, CLI (spec 221)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from forge_doctor.analyzers.metadata_model import metadata_model
from forge_doctor.analyzers.sql_ast import SQLGLOT_AVAILABLE
from forge_doctor.checks.metadata import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Confidence

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


_DH = [
    {
        "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.orders,PROD)",
        "aspects": [
            {"datasetProperties": {"description": "orders"}},
            {"ownership": {"owners": [{"owner": "urn:li:corpuser:de"}]}},
            {"globalTags": {"tags": [{"tag": "urn:li:tag:core"}]}},
        ],
    }
]


def _detected_table(tmp_path: Path, name: str = "orders") -> None:
    (tmp_path / "ddl.sql").write_text(f"CREATE TABLE {name} (id INT);\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Model


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "x.json").write_text('{"a": 1}', encoding="utf-8")
    assert not metadata_model(_ctx(tmp_path)).has_evidence


def test_datahub_export(tmp_path: Path) -> None:
    (tmp_path / "x.datahub.json").write_text(json.dumps(_DH), encoding="utf-8")
    model = metadata_model(_ctx(tmp_path))
    assert model.has_evidence
    d = model.datasets[0]
    assert d.vendor == "datahub"
    assert d.name == "orders"
    assert d.qualified == "dw.orders"
    assert d.environment == "PROD"
    assert d.platform == "mysql"
    assert d.owners
    assert d.has_description
    assert d.tags


def test_datahub_upstream_lineage(tmp_path: Path) -> None:
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.f,PROD)",
            "aspects": [
                {
                    "upstreamLineage": {
                        "upstreams": [
                            {
                                "dataset": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.a,PROD)",
                                "type": "TRANSFORMED",
                            }
                        ]
                    }
                }
            ],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    d = metadata_model(_ctx(tmp_path)).datasets[0]
    assert d.upstreams == ("a",)


def test_openmetadata_export(tmp_path: Path) -> None:
    (tmp_path / "e.ometa.json").write_text(
        json.dumps(
            [
                {
                    "entityType": "table",
                    "fullyQualifiedName": "svc.dw.orders",
                    "owner": {"name": "de"},
                    "tags": [{"tagFQN": "tier.gold"}],
                    "description": "orders table",
                }
            ]
        ),
        encoding="utf-8",
    )
    d = metadata_model(_ctx(tmp_path)).datasets[0]
    assert d.vendor == "openmetadata"
    assert d.name == "orders"
    assert d.owners == ("de",)
    assert d.has_description


def test_glue_catalog_export(tmp_path: Path) -> None:
    d = tmp_path / "glue"
    d.mkdir()
    (d / "tables.json").write_text(
        json.dumps({"TableList": [{"Name": "orders", "DatabaseName": "dw", "Description": "x"}]}),
        encoding="utf-8",
    )
    ds = metadata_model(_ctx(tmp_path)).datasets[0]
    assert ds.vendor == "glue"
    assert ds.qualified == "dw.orders"


def test_unity_catalog_export(tmp_path: Path) -> None:
    d = tmp_path / "unity"
    d.mkdir()
    (d / "tables.json").write_text(
        json.dumps([{"catalog_name": "main", "schema_name": "dw", "table_name": "orders"}]),
        encoding="utf-8",
    )
    ds = metadata_model(_ctx(tmp_path)).datasets[0]
    assert ds.vendor == "unity"
    assert ds.qualified == "main.dw.orders"


def test_generic_json_not_attributed(tmp_path: Path) -> None:
    (tmp_path / "report.json").write_text('{"entities": ["a"], "owner": "x"}', encoding="utf-8")
    assert not metadata_model(_ctx(tmp_path)).has_evidence


def test_recipe_connector_types_only(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "r.yml").write_text(
        "source:\n  type: mysql\n  config:\n    host: db.internal\n    password: s3cret\n"
        "sink:\n  type: datahub-rest\n",
        encoding="utf-8",
    )
    model = metadata_model(_ctx(tmp_path))
    assert model.recipes
    r = model.recipes[0]
    assert r.source_type == "mysql"
    # secrets never carried into the model (all str fields checked)
    fields = [r.source_type, r.sink_type]
    fields += [x for d in model.datasets for x in (*d.owners, *d.tags, d.name, d.qualified)]
    assert not any("s3cret" in f or "db.internal" in f for f in fields)


# ---------------------------------------------------------------------------
# Checks


def test_meta000_census(tmp_path: Path) -> None:
    (tmp_path / "x.datahub.json").write_text(json.dumps(_DH), encoding="utf-8")
    assert "META000" in _findings(tmp_path)


@needs_sqlglot
def test_meta001_stale_entry(tmp_path: Path) -> None:
    _detected_table(tmp_path)
    ghost = {
        "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.ghost,PROD)",
        "aspects": [],
    }
    (tmp_path / "x.datahub.json").write_text(json.dumps([*_DH, ghost]), encoding="utf-8")
    found = _findings(tmp_path)
    assert "META001" in found
    assert any("ghost" in m for m in found["META001"])
    assert not any("dw.orders" in m for m in found["META001"])


@needs_sqlglot
def test_meta002_coverage_gap(tmp_path: Path) -> None:
    _detected_table(tmp_path, "uncataloged_table")
    (tmp_path / "x.datahub.json").write_text(json.dumps(_DH), encoding="utf-8")
    found = _findings(tmp_path)
    assert "META002" in found
    assert any("uncataloged_table" in m for m in found["META002"])


def test_meta002_no_catalog_quiet(tmp_path: Path) -> None:
    _detected_table(tmp_path)
    assert "META002" not in _findings(tmp_path)


def test_meta003_no_owner(tmp_path: Path) -> None:
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.x,PROD)",
            "aspects": [{"datasetProperties": {"description": "x"}}],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    assert "META003" in _findings(tmp_path)


def test_meta003_owned_quiet(tmp_path: Path) -> None:
    (tmp_path / "x.datahub.json").write_text(json.dumps(_DH), encoding="utf-8")
    assert "META003" not in _findings(tmp_path)


def test_meta004_prod_undocumented(tmp_path: Path) -> None:
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.x,PROD)",
            "aspects": [{"ownership": {"owners": [{"owner": "u"}]}}],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    assert "META004" in _findings(tmp_path)


def test_meta004_dev_quiet(tmp_path: Path) -> None:
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.x,DEV)",
            "aspects": [],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    assert "META004" not in _findings(tmp_path)


@needs_sqlglot
def test_meta005_lineage_contradiction(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "INSERT INTO orders SELECT * FROM customers;\n", encoding="utf-8"
    )
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.orders,PROD)",
            "aspects": [
                {
                    "upstreamLineage": {
                        "upstreams": [
                            {"dataset": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.raw,PROD)"}
                        ]
                    }
                }
            ],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    ctx = _ctx(tmp_path)
    res = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "META005"]
    assert res
    assert res[0].confidence == Confidence.MEDIUM
    assert "raw" in res[0].message


@needs_sqlglot
def test_meta005_agreeing_lineage_quiet(tmp_path: Path) -> None:
    (tmp_path / "ddl.sql").write_text(
        "INSERT INTO orders SELECT * FROM customers;\n", encoding="utf-8"
    )
    doc = [
        {
            "entityUrn": "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.orders,PROD)",
            "aspects": [
                {
                    "upstreamLineage": {
                        "upstreams": [
                            {
                                "dataset": (
                                    "urn:li:dataset:(urn:li:dataPlatform:mysql,dw.customers,PROD)"
                                )
                            }
                        ]
                    }
                }
            ],
        }
    ]
    (tmp_path / "x.datahub.json").write_text(json.dumps(doc), encoding="utf-8")
    assert "META005" not in _findings(tmp_path)


# ---------------------------------------------------------------------------
# CLI


def test_catalog_inspect_cli(tmp_path: Path) -> None:
    (tmp_path / "x.datahub.json").write_text(json.dumps(_DH), encoding="utf-8")
    res = runner.invoke(app, ["catalog", "inspect", str(tmp_path)])
    assert res.exit_code == 0
    assert "dw.orders" in res.output
    assert "datahub" in res.output


def test_catalog_inspect_empty(tmp_path: Path) -> None:
    res = runner.invoke(app, ["catalog", "inspect", str(tmp_path)])
    assert res.exit_code == 0
