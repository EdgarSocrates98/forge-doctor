"""Capability graph: provenance wiring + CLI (spec 226)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityStatus,
)
from forge_doctor.core.capability_graph import capability_subgraph
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    RelKind,
)

runner = CliRunner()

_PACK = {
    "schema_version": 2,
    "pack_version": "2026.01.01",
    "verified_at": "2026-01-01",
    "capabilities": [
        {
            "id": "THING",
            "status": "supported",
            "versions": {"2.0": "supported"},
            "source": "https://example.test/docs",
        },
        {
            "id": "GATED",
            "status": "supported",
            "when": {"deployment": "serverless"},
            "source": "https://example.test/gated",
        },
    ],
}


def _registry() -> CapabilityRegistry:
    return CapabilityRegistry([("demo", "capabilities/demo", _PACK)])


def _graph_with_version(version: str = "2.0") -> DataPlatformGraph:
    g = DataPlatformGraph()
    g.add_entity(
        Entity(
            kind=EntityKind.COMPUTE_JOB,
            domain="demo",
            identifier="job1",
            attrs=(("engine_version", version),),
        )
    )
    return g


# --- provenance on results ---------------------------------------------------


def test_supported_carries_deciding_entry() -> None:
    result = _registry().evaluate("THING", CapabilityContext(platform="demo", version="2.0"))
    assert result.status is CapabilityStatus.SUPPORTED
    assert result.entry_id == "THING"
    assert result.pack == "capabilities/demo"
    assert result.pack_version == "2026.01.01"
    assert result.source == "https://example.test/docs"


def test_when_clause_provenance() -> None:
    result = _registry().evaluate(
        "GATED", CapabilityContext(platform="demo", attributes=(("deployment", "serverless"),))
    )
    assert result.status is CapabilityStatus.SUPPORTED
    assert result.matched_when == (("deployment", "serverless"),)


def test_unknown_names_missing_evidence_when_clause() -> None:
    result = _registry().evaluate(
        "GATED", CapabilityContext(platform="demo", attributes=(("deployment", "ec2"),))
    )
    assert result.status is CapabilityStatus.UNKNOWN
    assert "deployment=serverless" in result.missing_evidence


def test_unknown_names_missing_evidence_versions() -> None:
    result = _registry().evaluate("THING", CapabilityContext(platform="demo", version="9.9"))
    assert result.status is CapabilityStatus.UNKNOWN
    assert any("2.0" in m for m in result.missing_evidence)


# --- subgraph -----------------------------------------------------------------


def test_subgraph_links_capability_to_pack() -> None:
    sub = capability_subgraph(_graph_with_version(), _registry())
    cap_ids = {e.id for e in sub.entities() if e.kind is EntityKind.CAPABILITY}
    assert cap_ids == {"capability:demo:THING", "capability:demo:GATED"}
    edges = [r for r in sub.relationships() if r.kind is RelKind.EVIDENCED_BY]
    pack_edge = next(
        r for r in edges if r.src == "capability:demo:THING" and ":knowledge:" in r.dst
    )
    assert pack_edge.dst == "knowledge_pack:knowledge:capabilities/demo"
    assert dict(pack_edge.attrs)["entry_id"] == "THING"


def test_subgraph_edges_carry_version_evidence() -> None:
    sub = capability_subgraph(_graph_with_version(), _registry())
    version_edges = [
        r
        for r in sub.relationships()
        if r.src == "capability:demo:THING" and r.dst.startswith("compute_job:")
    ]
    assert len(version_edges) == 1
    assert dict(version_edges[0].attrs)["provides"] == "version"


def test_subgraph_deterministic() -> None:
    a = capability_subgraph(_graph_with_version(), _registry()).to_dict()
    b = capability_subgraph(_graph_with_version(), _registry()).to_dict()
    assert json.dumps(a) == json.dumps(b)


def test_subgraph_empty_for_unknown_domains() -> None:
    g = DataPlatformGraph()
    g.add_entity(Entity(kind=EntityKind.TABLE, domain="other", identifier="t"))
    assert capability_subgraph(g, _registry()).entities() == []


# --- CLI ----------------------------------------------------------------------


def test_cli_capabilities_graph(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["capabilities", "graph", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "dynamodb/DYNAMODB_STREAMS" in result.output


def test_cli_capabilities_graph_json(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["capabilities", "graph", str(tmp_path), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(e["kind"] == "capability" for e in data["entities"])
    assert all(r["kind"] == "EVIDENCED_BY" for r in data["relationships"])


def test_cli_list_json_default_shape_unchanged() -> None:
    result = runner.invoke(app, ["capabilities", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["dynamodb"]["DYNAMODB_STREAMS"] in {
        "supported",
        "unsupported",
        "conditional",
        "unknown",
    }


def test_cli_list_json_provenance() -> None:
    result = runner.invoke(app, ["capabilities", "list", "--json", "--provenance"])
    assert result.exit_code == 0
    row = json.loads(result.output)["dynamodb"]["DYNAMODB_STREAMS"]
    assert row["status"] == "supported"
    prov = row["provenance"]
    assert prov["entry_id"] == "DYNAMODB_STREAMS"
    assert prov["pack"] == "capabilities/dynamodb"


def test_cli_explain_json_provenance() -> None:
    result = runner.invoke(
        app, ["capabilities", "explain", "dynamodb", "DYNAMODB_STREAMS", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["entry_id"] == "DYNAMODB_STREAMS"
