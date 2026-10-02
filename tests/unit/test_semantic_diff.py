"""Semantic diff (roadmap-2 phase 6) - entity diff + blast radius + risk."""

from __future__ import annotations

from pathlib import Path

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
    RISK_MEDIUM,
    blast_radius,
    diff_graphs,
)


def _e(kind: EntityKind, domain: str, ident: str, file: str = "", **attrs: str) -> Entity:
    return Entity(
        kind=kind,
        domain=domain,
        identifier=ident,
        file=Path(file) if file else None,
        attrs=tuple(sorted(attrs.items())),
    )


def _platform() -> DataPlatformGraph:
    """infra -> glue job <- airflow task <- workflow."""
    g = DataPlatformGraph()
    infra = _e(EntityKind.INFRASTRUCTURE_RESOURCE, "aws", "aws_glue_job.orders", "main.tf")
    job = _e(EntityKind.COMPUTE_JOB, "glue", "orders-etl")
    task = _e(EntityKind.TASK, "airflow", "load", "dags/d.py")
    wf = _e(EntityKind.WORKFLOW, "airflow", "daily", "dags/d.py")
    for e in (infra, job, task, wf):
        g.add_entity(e)
    g.add_relationship(Relationship(infra.id, job.id, RelKind.DEFINES))
    g.add_relationship(Relationship(task.id, job.id, RelKind.INVOKES))
    g.add_relationship(Relationship(wf.id, task.id, RelKind.INVOKES))
    return g


def test_removed_entity_high_risk_with_dependents() -> None:
    base = _platform()
    head = DataPlatformGraph()
    diff = diff_graphs(base, head, frozenset({"main.tf"}))
    removed = diff.changes_of("removed")
    assert any(c.entity_id == "compute_job:glue:orders-etl" for c in removed)
    assert diff.risk == RISK_HIGH
    # job removal reaches the task that invokes it AND the workflow
    assert "task:airflow:load" in diff.impacted_entities
    assert "workflow:airflow:daily" in diff.impacted_entities


def test_modified_attr_medium_or_high_by_kind() -> None:
    base = _platform()
    head = _platform()
    # replace the compute job with a bumped version attr
    job = _e(EntityKind.COMPUTE_JOB, "glue", "orders-etl", glue_version="5.0")
    head.add_entity(job)
    diff = diff_graphs(base, head, frozenset())
    modified = diff.changes_of("modified")
    assert modified and modified[0].attr_diffs == ("glue_version",)
    assert diff.risk == RISK_HIGH  # structural kind + dependents


def test_added_entity_never_high(tmp_path: Path) -> None:
    base = _platform()
    head = _platform()
    head.add_entity(_e(EntityKind.TABLE, "iceberg", "new_t", "tables/new.sql"))
    diff = diff_graphs(base, head, frozenset({"tables/new.sql"}))
    assert diff.changes_of("added")
    assert diff.risk == RISK_LOW


def test_file_touched_but_semantics_unchanged() -> None:
    base = _platform()
    head = _platform()  # identical graphs
    diff = diff_graphs(base, head, frozenset({"main.tf", "dags/d.py"}))
    touched = diff.changes_of("touched")
    assert {c.entity_id for c in touched} == {
        "infrastructure_resource:aws:aws_glue_job.orders",
        "task:airflow:load",
        "workflow:airflow:daily",
    }
    assert diff.risk == RISK_MEDIUM  # touched entities have dependents
    assert diff.unmapped_files == ()


def test_unmapped_files_reported(tmp_path: Path) -> None:
    base = head = _platform()
    diff = diff_graphs(base, head, frozenset({"README.txt", "main.tf"}))
    assert diff.unmapped_files == ("README.txt",)


def test_blast_radius_follows_dependency_direction() -> None:
    g = _platform()
    # removing the job breaks the invoking task + containing workflow;
    # the defining infra resource is NOT downstream of the job.
    assert blast_radius(g, "compute_job:glue:orders-etl") == {
        "task:airflow:load",
        "workflow:airflow:daily",
    }
    # but the infra resource owns the job - its removal cascades fully
    assert blast_radius(g, "infrastructure_resource:aws:aws_glue_job.orders") == {
        "compute_job:glue:orders-etl",
        "task:airflow:load",
        "workflow:airflow:daily",
    }


def test_diff_deterministic() -> None:
    base, head = _platform(), _platform()
    head.add_entity(_e(EntityKind.TABLE, "iceberg", "x", "x.sql"))
    a = diff_graphs(base, head, frozenset({"x.sql"}))
    b = diff_graphs(base, head, frozenset({"x.sql"}))
    assert [(c.entity_id, c.change, c.impacted) for c in a.changes] == [
        (c.entity_id, c.change, c.impacted) for c in b.changes
    ]
