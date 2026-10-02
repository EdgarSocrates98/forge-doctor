"""Data contracts + schema evolution (spec 217): model, checks, diff."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.analyzers.datacontract_model import datacontract_model, norm_family
from forge_doctor.checks.datacontract import CHECKS
from forge_doctor.core.context import ProjectContext
from forge_doctor.core.models import Severity
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor.core.semantic_diff import (
    RISK_HIGH,
    RISK_LOW,
    _type_relation,
    blast_radius,
    diff_graphs,
)


def _ctx(tmp_path: Path) -> ProjectContext:
    return ProjectContext(root=tmp_path)


_CONTRACT = """\
dataContractSpecification: 1.1.0
id: orders-contract
info:
  owner: analytics-team
servers:
  prod:
    type: snowflake
    environment: prod
servicelevels:
  availability:
    percentage: "99.9"
schema:
  - name: orders
    fields:
      order_id:
        type: bigint
        required: true
      amount:
        type: decimal(10,2)
      note:
        type: varchar
"""

_ODCS = """\
apiVersion: v3.0.0
kind: DataContract
id: payments-odcs
name: payments
servers:
  - server: prod
    type: snowflake
schema:
  - name: payments
    properties:
      - name: payment_id
        logicalType: string
        required: true
      - name: total
        logicalType: number
"""


def _write_contract(tmp_path: Path, text: str = _CONTRACT, name: str = "datacontract.yml") -> None:
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_no_contract_files_no_evidence(tmp_path: Path) -> None:
    """Generic yaml without markers -> adversarial silence."""
    (tmp_path / "config.yml").write_text("version: 2\ntables:\n  - name: t\n", encoding="utf-8")
    (tmp_path / "q.sql").write_text("SELECT 1;\n", encoding="utf-8")
    model = datacontract_model(_ctx(tmp_path))
    assert not model.has_evidence
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx) if f.severity != Severity.PASS}
    assert not found


def test_datacontract_cli_parse(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    model = datacontract_model(_ctx(tmp_path))
    assert model.has_evidence
    c = model.contracts[0]
    assert c.id == "orders-contract"
    assert c.format == "datacontract"
    assert c.owner == "analytics-team"
    assert c.schema_fields == 3
    assert "availability" in c.sla
    fields = {f.name: f for f in c.objects[0].fields}
    assert fields["order_id"].type == "bigint"
    assert fields["order_id"].required
    assert fields["amount"].type == "decimal"
    assert fields["note"].type == "text"
    assert c.claims_production()


def test_odcs_parse(tmp_path: Path) -> None:
    _write_contract(tmp_path, _ODCS, "payments.odcs.yaml")
    model = datacontract_model(_ctx(tmp_path))
    c = model.contracts[0]
    assert c.format == "odcs"
    assert c.id == "payments-odcs"
    assert c.schema_fields == 2
    types = {f.name: f.type for f in c.objects[0].fields}
    assert types["payment_id"] == "text"
    assert types["total"] == "decimal"


def test_marker_inside_generic_name(tmp_path: Path) -> None:
    """``contracts/foo.yml`` with a spec key is a contract too."""
    _write_contract(tmp_path, _CONTRACT, "orders.yml")
    assert datacontract_model(_ctx(tmp_path)).has_evidence


def test_unsupported_sections_recorded(tmp_path: Path) -> None:
    _write_contract(tmp_path, _CONTRACT + "terms:\n  usage: internal\n")
    c = datacontract_model(_ctx(tmp_path)).contracts[0]
    assert "terms" in c.unsupported


def test_dctr001_missing_schema(tmp_path: Path) -> None:
    _write_contract(
        tmp_path,
        "dataContractSpecification: 1.1.0\nid: blank\nservers:\n  dev:\n    type: s3\n",
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DCTR001" in found
    assert "DCTR002" not in found  # dev server, not prod


def test_dctr002_prod_without_sla(tmp_path: Path) -> None:
    _write_contract(
        tmp_path, _CONTRACT.replace('servicelevels:\n  availability:\n    percentage: "99.9"\n', "")
    )
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DCTR002" in found


def test_dctr002_with_sla_quiet(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DCTR002" not in found


def test_dctr003_type_drift(tmp_path: Path) -> None:
    _write_contract(tmp_path)
    (tmp_path / "ddl.sql").write_text(
        "CREATE TABLE orders (order_id BIGINT, amount VARCHAR(32), note VARCHAR(64));\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    found = [f for c in CHECKS for f in c.run(ctx) if f.check_id == "DCTR003"]
    assert found
    assert any("amount" in f.message for f in found)
    assert not any("order_id" in f.message for f in found)
    assert not any("note" in f.message for f in found)  # varchar vs varchar: same family


def test_dctr003_no_detected_schema_quiet(tmp_path: Path) -> None:
    _write_contract(tmp_path)  # contract but no DDL/TF/exports anywhere
    ctx = _ctx(tmp_path)
    found = {f.check_id for c in CHECKS for f in c.run(ctx)}
    assert "DCTR003" not in found


def test_graph_contract_governs_relation(tmp_path: Path) -> None:
    from forge_doctor.analyzers.platform_graph_builder import build_platform_graph

    _write_contract(tmp_path)
    (tmp_path / "ddl.sql").write_text("CREATE TABLE orders (order_id BIGINT);\n", encoding="utf-8")
    g = build_platform_graph(_ctx(tmp_path))
    contract = g.entity("data_contract:datacontract:orders-contract")
    assert contract is not None
    out = {(r.dst, r.kind.value) for r in g.outbound(contract.id)}
    governs = {dst for dst, kind in out if kind == "GOVERNS"}
    assert governs
    target = g.entity(next(iter(governs)))
    assert target is not None
    attrs = dict(target.attrs)
    assert attrs.get("field.order_id") == "bigint"
    assert attrs.get("contract") == "orders-contract"


def _contract_graph(g: DataPlatformGraph, fields: dict[str, str]) -> DataPlatformGraph:
    """Table carrying contract field.* attrs + a consumer that reads it."""
    table = Entity(
        kind=EntityKind.TABLE,
        domain="snowflake",
        identifier="db.orders",
        attrs=tuple(sorted((f"field.{k}", v) for k, v in fields.items())),
    )
    consumer = Entity(kind=EntityKind.DBT_MODEL, domain="dbt", identifier="mart_orders")
    g.add_entity(table)
    g.add_entity(consumer)
    g.add_relationship(Relationship(consumer.id, table.id, RelKind.READS_FROM))
    return g


def test_diff_field_removed_breaking(tmp_path: Path) -> None:
    base = _contract_graph(DataPlatformGraph(), {"order_id": "bigint", "amount": "decimal"})
    head = _contract_graph(DataPlatformGraph(), {"order_id": "bigint"})
    diff = diff_graphs(base, head, frozenset())
    mod = diff.changes_of("modified")
    assert mod and mod[0].breaking == ("amount removed",)
    assert diff.risk == RISK_HIGH
    assert "dbt_model:dbt:mart_orders" in mod[0].impacted  # blast to consumer


def test_diff_narrowed_type_breaking(tmp_path: Path) -> None:
    base = _contract_graph(DataPlatformGraph(), {"note": "varchar(64)"})
    head = _contract_graph(DataPlatformGraph(), {"note": "varchar(16)"})
    diff = diff_graphs(base, head, frozenset())
    mod = diff.changes_of("modified")
    assert mod and "narrowed" in mod[0].breaking[0]


def test_diff_widened_not_breaking(tmp_path: Path) -> None:
    base = _contract_graph(DataPlatformGraph(), {"note": "varchar(16)"})
    head = _contract_graph(DataPlatformGraph(), {"note": "varchar(64)"})
    diff = diff_graphs(base, head, frozenset())
    mod = diff.changes_of("modified")
    assert mod and not mod[0].breaking
    assert diff.risk != RISK_HIGH or not any("breaking" in r for r in diff.reasons)


def test_diff_added_field_additive(tmp_path: Path) -> None:
    base = _contract_graph(DataPlatformGraph(), {"order_id": "bigint"})
    head = _contract_graph(DataPlatformGraph(), {"order_id": "bigint", "tag": "varchar(8)"})
    diff = diff_graphs(base, head, frozenset())
    mod = diff.changes_of("modified")
    assert mod and not mod[0].breaking
    assert diff.risk == RISK_LOW or all("breaking" not in r for r in diff.reasons)


def test_diff_removed_contracted_relation_high(tmp_path: Path) -> None:
    base = _contract_graph(DataPlatformGraph(), {"order_id": "bigint"})
    # consumer stays, governed table drops out of head
    head_g = DataPlatformGraph()
    head_g.add_entity(Entity(kind=EntityKind.DBT_MODEL, domain="dbt", identifier="mart_orders"))
    diff = diff_graphs(base, head_g, frozenset())
    removed = {c.entity_id for c in diff.changes_of("removed")}
    assert "table:snowflake:db.orders" in removed
    assert diff.risk == RISK_HIGH


def test_blast_radius_reads_from_consumers() -> None:
    g = _contract_graph(DataPlatformGraph(), {"a": "int"})
    assert blast_radius(g, "table:snowflake:db.orders") == {"dbt_model:dbt:mart_orders"}


def test_norm_family() -> None:
    assert norm_family("VARCHAR(64)") == "text"
    assert norm_family("decimal(10, 2)") == "decimal"
    assert norm_family("STRUCT<a INT>") == "struct"
    assert norm_family("INT") == "integer"


def test_type_relation() -> None:
    assert _type_relation("int", "int") == "same"
    assert _type_relation("varchar(32)", "varchar(64)") == "widened"
    assert _type_relation("varchar(64)", "varchar(32)") == "narrowed"
    assert _type_relation("int", "bigint") == "widened"
    assert _type_relation("bigint", "int") == "narrowed"
    assert _type_relation("decimal(10,2)", "decimal(10,4)") == "widened"
    assert _type_relation("decimal(10,2)", "decimal(9,1)") == "narrowed"
    assert _type_relation("varchar", "int") == "changed"
    assert _type_relation("date", "timestamp") == "widened"
