"""Search platforms: model, SRCH checks, CLI (spec 220)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.analyzers.search_model import search_model
from forge_doctor.checks.search import CHECKS
from forge_doctor.cli.app import app
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity

runner = CliRunner()


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


def _findings(tmp_path: Path) -> dict[str, str]:
    ctx = _ctx(tmp_path)
    return {f.check_id: f.message for c in CHECKS for f in c.run(ctx)}


_TEMPLATE = {
    "name": "prod-events",
    "index_patterns": ["prod-events-*"],
    "template": {
        "settings": {"index": {"number_of_shards": 3, "number_of_replicas": 1}},
        "mappings": {"properties": {"msg": {"type": "text"}}},
    },
}


def _write_template(tmp_path: Path, doc: dict, name: str = "idx.template.json") -> None:
    (tmp_path / name).write_text(json.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# Model


def test_no_evidence_empty(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text('{"a": 1}', encoding="utf-8")
    assert not search_model(_ctx(tmp_path)).has_evidence


def test_index_template_parsed(tmp_path: Path) -> None:
    _write_template(tmp_path, _TEMPLATE)
    model = search_model(_ctx(tmp_path))
    assert model.has_evidence
    idx = model.indices[0]
    assert idx.name == "prod-events"
    assert idx.kind == "template"
    assert idx.index_patterns == ("prod-events-*",)
    assert idx.replicas == "1"
    assert "text" in idx.field_types


def test_bare_mappings_key_not_attributed(tmp_path: Path) -> None:
    """A generic JSON with a 'mappings' key needs compound evidence."""
    (tmp_path / "card.json").write_text(
        json.dumps({"mappings": {"input": "tensor"}}), encoding="utf-8"
    )
    assert not search_model(_ctx(tmp_path)).has_evidence


def test_mappings_in_search_dir_attributed(tmp_path: Path) -> None:
    """A mappings doc inside an elasticsearch/ dir is claimed (dir signal)."""
    d = tmp_path / "elasticsearch"
    d.mkdir()
    (d / "orders.json").write_text(
        json.dumps({"mappings": {"properties": {"id": {"type": "keyword"}}}}),
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    assert model.indices
    assert model.indices[0].vendor == "elasticsearch"


def test_ism_policy_vendor(tmp_path: Path) -> None:
    (tmp_path / "hot-policy.json").write_text(
        json.dumps(
            {
                "policy": {
                    "ism_template": {"index_patterns": ["logs-*"]},
                    "states": [{"name": "hot"}, {"name": "delete"}],
                }
            }
        ),
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    pol = model.policies[0]
    assert pol.kind == "ism" and pol.vendor == "opensearch"
    assert pol.has_retention


def test_ilm_policy_vendor(tmp_path: Path) -> None:
    (tmp_path / "ilm-policy.json").write_text(
        json.dumps({"policy": {"phases": {"hot": {}, "delete": {"min_age": "30d"}}}}),
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    pol = model.policies[0]
    assert pol.kind == "ilm" and pol.vendor == "elasticsearch"


def test_pipeline_parsed(tmp_path: Path) -> None:
    (tmp_path / "ingest-pipeline.json").write_text(
        json.dumps({"processors": [{"grok": {}}, {"date": {}}]}),
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    assert model.pipelines[0].processors == ("date", "grok")


def test_terraform_domain(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_opensearch_domain" "logs" {\n'
        '  domain_name = "logs"\n'
        "  encrypt_at_rest { enabled = true }\n"
        "  node_to_node_encryption { enabled = false }\n"
        "}\n",
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    d = model.domains[0]
    assert d.vendor == "opensearch"
    assert d.encryption_at_rest == "true"
    assert d.node_to_node == "false"


def test_observed_cluster_export(tmp_path: Path) -> None:
    _write_template(tmp_path, _TEMPLATE)
    d = tmp_path / "opensearch"
    d.mkdir()
    (d / "cluster.json").write_text(
        json.dumps({"cluster_name": "prod", "status": "yellow", "number_of_nodes": 3}),
        encoding="utf-8",
    )
    model = search_model(_ctx(tmp_path))
    assert model.observed[0].vendor == "opensearch"
    assert model.observed[0].get("status") == "yellow"


# ---------------------------------------------------------------------------
# Checks


def test_srch000_census(tmp_path: Path) -> None:
    _write_template(tmp_path, _TEMPLATE)
    assert "SRCH000" in _findings(tmp_path)


def test_srch001_prod_no_replicas(tmp_path: Path) -> None:
    doc = json.loads(json.dumps(_TEMPLATE))
    doc["template"]["settings"]["index"].pop("number_of_replicas")
    _write_template(tmp_path, doc)
    assert "SRCH001" in _findings(tmp_path)


def test_srch001_nonprod_quiet(tmp_path: Path) -> None:
    doc = json.loads(json.dumps(_TEMPLATE))
    doc["name"] = "dev-events"
    doc["index_patterns"] = ["dev-*"]
    doc["template"]["settings"]["index"].pop("number_of_replicas")
    _write_template(tmp_path, doc)
    assert "SRCH001" not in _findings(tmp_path)


def test_srch002_wildcard_no_lifecycle(tmp_path: Path) -> None:
    doc = json.loads(json.dumps(_TEMPLATE))
    doc["name"] = "logs-tpl"
    doc["index_patterns"] = ["logs-*"]
    _write_template(tmp_path, doc)
    assert "SRCH002" in _findings(tmp_path)


def test_srch002_policy_covers_quiet(tmp_path: Path) -> None:
    doc = json.loads(json.dumps(_TEMPLATE))
    doc["name"] = "logs-tpl"
    doc["index_patterns"] = ["logs-*"]
    _write_template(tmp_path, doc)
    (tmp_path / "ism-policy.json").write_text(
        json.dumps(
            {
                "policy": {
                    "ism_template": {"index_patterns": ["logs-*"]},
                    "states": [{"name": "hot"}, {"name": "delete"}],
                }
            }
        ),
        encoding="utf-8",
    )
    assert "SRCH002" not in _findings(tmp_path)


def test_srch003_mapping_explosion(tmp_path: Path) -> None:
    props = {
        f"o{i}": {"type": "object", "properties": {"k": {"type": "keyword"}}} for i in range(7)
    }
    doc = {
        "name": "events",
        "index_patterns": ["events-*"],
        "template": {"mappings": {"properties": props}},
    }
    _write_template(tmp_path, doc)
    assert "SRCH003" in _findings(tmp_path)


def test_srch003_dynamic_strict_quiet(tmp_path: Path) -> None:
    props = {
        f"o{i}": {"type": "object", "properties": {"k": {"type": "keyword"}}} for i in range(7)
    }
    doc = {
        "name": "events",
        "index_patterns": ["events-*"],
        "template": {"mappings": {"dynamic": "strict", "properties": props}},
    }
    _write_template(tmp_path, doc)
    assert "SRCH003" not in _findings(tmp_path)


def test_srch004_domain_unencrypted(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_opensearch_domain" "x" {\n  domain_name = "x"\n}\n',
        encoding="utf-8",
    )
    found = _findings(tmp_path)
    assert "SRCH004" in found
    assert "opensearch" in found["SRCH004"]


def test_srch004_encrypted_quiet(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_opensearch_domain" "x" {\n'
        "  encrypt_at_rest { enabled = true }\n"
        "  node_to_node_encryption { enabled = true }\n"
        "}\n",
        encoding="utf-8",
    )
    assert "SRCH004" not in _findings(tmp_path)


def test_srchnnn_silent_on_empty(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    res = [f for c in CHECKS for f in c.run(ctx)]
    assert all(f.severity == Severity.PASS for f in res)
    assert len(res) == 1 and res[0].check_id == "SRCH000"


# ---------------------------------------------------------------------------
# CLI


def test_search_inspect_cli(tmp_path: Path) -> None:
    _write_template(tmp_path, _TEMPLATE)
    res = runner.invoke(app, ["search", "inspect", str(tmp_path)])
    assert res.exit_code == 0
    assert "prod-events" in res.output


def test_search_inspect_empty(tmp_path: Path) -> None:
    res = runner.invoke(app, ["search", "inspect", str(tmp_path)])
    assert res.exit_code == 0
