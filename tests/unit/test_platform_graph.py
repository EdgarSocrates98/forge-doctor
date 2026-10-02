"""Unit tests for DataPlatformGraph (spec 171)."""

from __future__ import annotations

import pytest

from forge_doctor.core.models import EvidenceKind
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
    entities_of,
)


def ent(kind: EntityKind, domain: str, ident: str, **kw: object) -> Entity:
    return Entity(kind=kind, domain=domain, identifier=ident, **kw)  # type: ignore[arg-type]


def rel(src: Entity, dst: Entity, kind: RelKind, **kw: object) -> Relationship:
    return Relationship(src=src.id, dst=dst.id, kind=kind, **kw)  # type: ignore[arg-type]


def test_entity_id_canonical_format() -> None:
    e = ent(EntityKind.WORKFLOW, "airflow", "orders")
    assert e.id == "workflow:airflow:orders"
    # Different domains/kinds with the same name never collide.
    assert e.id != ent(EntityKind.TABLE, "iceberg", "orders").id
    assert e.id != ent(EntityKind.WORKFLOW, "controlm", "orders").id


def test_name_defaults_to_identifier() -> None:
    e = ent(EntityKind.TABLE, "iceberg", "glue.sales.orders")
    assert e.name == ""
    assert e.id == "table:iceberg:glue.sales.orders"


def test_attrs_tuple_lookup() -> None:
    e = ent(EntityKind.COMPUTE_JOB, "glue", "j", attrs=(("version", "5.1"),))
    assert e.attr("version") == "5.1"
    assert e.attr("missing") == ""


def test_duplicate_entity_first_wins() -> None:
    g = DataPlatformGraph()
    a = ent(EntityKind.TASK, "airflow", "t1", name="first")
    b = ent(EntityKind.TASK, "airflow", "t1", name="second")
    g.add_entity(a)
    g.add_entity(b)
    assert g.entity(a.id) is a


def test_edge_dedup_and_unknown_entity() -> None:
    g = DataPlatformGraph()
    a, b = ent(EntityKind.TASK, "a", "1"), ent(EntityKind.TASK, "a", "2")
    g.add_entity(a)
    g.add_entity(b)
    r = rel(a, b, RelKind.INVOKES)
    assert g.add_relationship(r) is True
    assert g.add_relationship(r) is False
    ghost = ent(EntityKind.TASK, "a", "ghost")
    with pytest.raises(KeyError):
        g.add_relationship(rel(ghost, b, RelKind.INVOKES))


def test_neighbors_inbound_outbound() -> None:
    g = DataPlatformGraph()
    dag = ent(EntityKind.WORKFLOW, "airflow", "d")
    t1, t2 = ent(EntityKind.TASK, "airflow", "t1"), ent(EntityKind.TASK, "airflow", "t2")
    for e in (dag, t1, t2):
        g.add_entity(e)
    g.add_relationship(rel(t1, dag, RelKind.DEPENDS_ON))
    g.add_relationship(rel(t1, t2, RelKind.INVOKES))
    assert [r.dst for r in g.outbound(t1.id, RelKind.INVOKES)] == [t2.id]
    assert [r.src for r in g.inbound(dag.id)] == [t1.id]
    assert g.neighbors(t1.id) == {dag.id, t2.id}


def test_reachable_cycle_safe() -> None:
    g = DataPlatformGraph()
    a, b, c = (ent(EntityKind.TASK, "x", n) for n in "abc")
    for e in (a, b, c):
        g.add_entity(e)
    g.add_relationship(rel(a, b, RelKind.INVOKES))
    g.add_relationship(rel(b, c, RelKind.INVOKES))
    g.add_relationship(rel(c, a, RelKind.INVOKES))  # cycle
    assert g.reachable(a.id) == {b.id, c.id}
    assert g.reachable(c.id, direction="in") == {b.id, a.id}


def test_determinism_across_insertion_orders() -> None:
    ents = [
        ent(EntityKind.TABLE, "iceberg", "t"),
        ent(EntityKind.WORKFLOW, "airflow", "d"),
        ent(EntityKind.STREAM, "spark", "s"),
    ]
    edges = [
        rel(ents[1], ents[0], RelKind.WRITES),
        rel(ents[2], ents[0], RelKind.PRODUCES),
    ]
    g1, g2 = DataPlatformGraph(), DataPlatformGraph()
    for e in ents:
        g1.add_entity(e)
    for e in reversed(ents):
        g2.add_entity(e)
    for r in edges:
        g1.add_relationship(r)
        g2.add_relationship(r)
    assert g1.to_dict() == g2.to_dict()


def test_serialization_shape() -> None:
    g = DataPlatformGraph()
    tbl = ent(EntityKind.TABLE, "iceberg", "glue.sales.orders", name="orders")
    job = ent(EntityKind.COMPUTE_JOB, "glue", "orders-etl")
    g.add_entity(tbl)
    g.add_entity(job)
    g.add_relationship(rel(job, tbl, RelKind.WRITES, evidence_kind=EvidenceKind.STATIC))
    doc = g.to_dict()
    assert doc["entities"][0]["id"] == "compute_job:glue:orders-etl"
    assert doc["entities"][1]["name"] == "orders"
    edge = doc["relationships"][0]
    assert edge["kind"] == "WRITES"
    assert edge["evidence_kind"] == "static"


def test_entities_of_skips_unknown() -> None:
    g = DataPlatformGraph()
    a = ent(EntityKind.TABLE, "iceberg", "t")
    g.add_entity(a)
    assert entities_of(g, [a.id, "table:iceberg:ghost"]) == [a]


def test_enum_surface_matches_spec() -> None:
    assert {k.value for k in EntityKind} == {
        "workflow",
        "task",
        "compute_job",
        "query",
        "dataset",
        "table",
        "stream",
        "catalog",
        "storage_location",
        "principal",
        "infrastructure_resource",
        "database",
        "graph",
        "graph_node",
        "graph_edge",
        "repo",
    }
    assert {k.value for k in RelKind} == {
        "INVOKES",
        "READS",
        "WRITES",
        "DEFINES",
        "GOVERNS",
        "STORED_IN",
        "DEPENDS_ON",
        "TRIGGERS",
        "PRODUCES",
        "CONSUMES",
        "IMPLEMENTS",
    }
