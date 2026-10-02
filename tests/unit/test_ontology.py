"""Platform ontology: vocabulary completeness, conformance, CLI (spec 225)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from forge_doctor.cli.app import app
from forge_doctor.core import incremental
from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.ontology import (
    capability_families,
    entity_kinds,
    evidence_domains,
    evidence_planes,
    producer_domains,
    relationship_kinds,
    validate_graph,
    vocabulary,
)
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)

runner = CliRunner()
DOCS = Path(__file__).resolve().parents[2] / "docs" / "ontology.md"


# --- vocabulary completeness ------------------------------------------------


def test_every_entity_kind_defined() -> None:
    assert {t.name for t in entity_kinds()} == {k.value for k in EntityKind}
    assert all(t.definition for t in entity_kinds())


def test_every_rel_kind_defined() -> None:
    assert {t.name for t in relationship_kinds()} == {k.value for k in RelKind}
    assert all(t.definition for t in relationship_kinds())


def test_every_evidence_plane_defined() -> None:
    assert {t.name for t in evidence_planes()} == {k.value for k in EvidenceKind}


def test_every_incremental_domain_defined() -> None:
    declared = {
        getattr(incremental, name)
        for name in dir(incremental)
        if name.isupper() and isinstance(getattr(incremental, name), str)
    } - {"ALWAYS_RUN"}
    assert declared <= {t.name for t in evidence_domains()}


def test_vocabulary_is_deterministic_and_serializable() -> None:
    a, b = vocabulary(), vocabulary()
    assert a == b
    assert json.dumps(a) == json.dumps(b)
    # stable section keys
    assert list(a) == [
        "capability_families",
        "entity_kinds",
        "evidence_domains",
        "evidence_planes",
        "producer_domains",
        "relationship_kinds",
        "consistency_models",
        "data_access_patterns",
        "data_movement_modes",
        "lifecycle_states",
        "materialization_kinds",
        "ownership_sources",
        "platform_kinds",
        "workload_intents",
    ]


def test_capability_families_nonempty_sorted() -> None:
    fams = capability_families()
    assert fams == tuple(sorted(fams))
    assert "glue" in fams  # bundled pack sanity


def test_producer_domains_sorted() -> None:
    names = [t.name for t in producer_domains()]
    assert names == sorted(names)
    assert "glue" in names and "workspace" in names


# --- conformance validation --------------------------------------------------


def _graph(domain: str = "glue") -> DataPlatformGraph:
    g = DataPlatformGraph()
    a = Entity(kind=EntityKind.COMPUTE_JOB, domain=domain, identifier="job1")
    b = Entity(kind=EntityKind.TABLE, domain="dynamodb", identifier="t1")
    g.add_entity(a)
    g.add_entity(b)
    g.add_relationship(Relationship(src=a.id, dst=b.id, kind=RelKind.WRITES))
    return g


def test_validate_graph_clean() -> None:
    assert validate_graph(_graph()) == []


def test_validate_graph_flags_unknown_domain() -> None:
    violations = validate_graph(_graph(domain="made-up-domain"))
    assert len(violations) == 1
    assert "made-up-domain" in violations[0]


# --- CLI ---------------------------------------------------------------------


def test_cli_ontology_lists_vocabulary() -> None:
    result = runner.invoke(app, ["ontology"])
    assert result.exit_code == 0, result.output
    for needle in ("entity kinds", "workflow", "INVOKES", "static", "producer domains"):
        assert needle in result.output


def test_cli_ontology_json() -> None:
    result = runner.invoke(app, ["ontology", "-f", "json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert any(t["name"] == "table" for t in data["entity_kinds"])


def test_cli_ontology_validate(tmp_path: Path) -> None:
    (tmp_path / "main.tf").write_text(
        'resource "aws_dynamodb_table" "t" { name = "t" }\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["ontology", "validate", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "ontology clean" in result.output


# --- doc parity ---------------------------------------------------------------


def _doc_section(doc: str, name: str) -> list[str]:
    m = re.search(rf"<!-- BEGIN {name} -->(.*?)<!-- END {name} -->", doc, re.DOTALL)
    assert m, f"missing <!-- BEGIN {name} --> section"
    rows = [
        row.split("|")[1].strip()
        for row in m.group(1).splitlines()
        if row.startswith("|") and not row.startswith("|-")
    ]
    return rows[1:]  # drop the header row


def test_ontology_doc_matches_vocabulary() -> None:
    doc = DOCS.read_text(encoding="utf-8")
    assert _doc_section(doc, "entity_kinds") == [t.name for t in entity_kinds()]
    assert _doc_section(doc, "relationship_kinds") == [t.name for t in relationship_kinds()]
    assert _doc_section(doc, "evidence_planes") == [t.name for t in evidence_planes()]
    assert _doc_section(doc, "evidence_domains") == [t.name for t in evidence_domains()]
    assert _doc_section(doc, "producer_domains") == [t.name for t in producer_domains()]
    # semantic-model sections (spec 230) — same parity rule
    vocab = vocabulary()
    for section in (
        "platform_kinds",
        "workload_intents",
        "data_access_patterns",
        "materialization_kinds",
        "consistency_models",
        "data_movement_modes",
        "lifecycle_states",
        "ownership_sources",
    ):
        assert _doc_section(doc, section) == [t["name"] for t in vocab[section]]
