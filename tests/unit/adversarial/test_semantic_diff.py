"""Semantic diff adversarial tests - identical graphs, unmapped churn,
determinism under reordering, risk monotonicity."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)
from forge_doctor.core.semantic_diff import RISK_HIGH, RISK_LOW, diff_graphs


def _e(kind: EntityKind, domain: str, ident: str, file: str = "", **attrs: str) -> Entity:
    return Entity(
        kind=kind,
        domain=domain,
        identifier=ident,
        file=Path(file) if file else None,
        attrs=tuple(sorted(attrs.items())),
    )


def test_identical_graphs_zero_risk() -> None:
    g = DataPlatformGraph()
    g.add_entity(_e(EntityKind.TABLE, "iceberg", "t", "t.sql"))
    diff = diff_graphs(g, g, frozenset())
    assert diff.changes == ()
    assert diff.risk == RISK_LOW
    assert diff.reasons == ("no platform entity changed",)


def test_add_remove_never_crash_on_missing_entity() -> None:
    """Removed entities are absent from head; modified ones need both."""
    base = DataPlatformGraph()
    head = DataPlatformGraph()
    base.add_entity(_e(EntityKind.STREAM, "kafka", "gone", "s.tf"))
    head.add_entity(_e(EntityKind.STREAM, "kafka", "new", "s.tf"))
    diff = diff_graphs(base, head, frozenset({"s.tf"}))
    kinds = {c.change for c in diff.changes}
    assert kinds == {"removed", "added"}


def test_entity_addition_order_does_not_change_risk() -> None:
    """Same facts, different insertion order -> identical diff."""

    def build(order: list[str]) -> DataPlatformGraph:
        g = DataPlatformGraph()
        ents = {
            "job": _e(EntityKind.COMPUTE_JOB, "glue", "j", "main.tf"),
            "wf": _e(EntityKind.WORKFLOW, "airflow", "w", "d.py"),
        }
        for k in order:
            g.add_entity(ents[k])
        g.add_relationship(
            Relationship("workflow:airflow:w", "compute_job:glue:j", RelKind.INVOKES)
        )
        return g

    base = build(["job", "wf"])
    # head drops the workflow invocation -> job loses its caller
    head2 = DataPlatformGraph()
    head2.add_entity(_e(EntityKind.COMPUTE_JOB, "glue", "j", "main.tf"))
    a = diff_graphs(base, head2, frozenset({"d.py"}))
    b = diff_graphs(build(["wf", "job"]), head2, frozenset({"d.py"}))
    assert a.risk == b.risk
    assert [c.entity_id for c in a.changes] == [c.entity_id for c in b.changes]


def test_risk_is_monotone_not_max_of_worst() -> None:
    """A HIGH removed-with-dependents plus a LOW add keeps HIGH."""
    base = DataPlatformGraph()
    job = _e(EntityKind.COMPUTE_JOB, "glue", "j")
    task = _e(EntityKind.TASK, "airflow", "t")
    base.add_entity(job)
    base.add_entity(task)
    base.add_relationship(Relationship(task.id, job.id, RelKind.INVOKES))
    head = DataPlatformGraph()  # everything removed
    head.add_entity(_e(EntityKind.TABLE, "iceberg", "new"))
    diff = diff_graphs(base, head, frozenset())
    assert diff.risk == RISK_HIGH
    assert any("removed" in r for r in diff.reasons)


def test_no_entity_means_no_panic(tmp_path: Path) -> None:
    """Empty graphs + changed files -> LOW risk, files unmapped."""
    diff = diff_graphs(DataPlatformGraph(), DataPlatformGraph(), frozenset({"x.py"}))
    assert diff.risk == RISK_LOW
    assert diff.unmapped_files == ("x.py",)
