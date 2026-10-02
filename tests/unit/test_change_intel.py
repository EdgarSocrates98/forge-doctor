"""Change intelligence (roadmap-3 spec 206) - capability diff + migration requirements."""

from __future__ import annotations

from pathlib import Path

from forge_doctor.core.capabilities import CapabilityStatus
from forge_doctor.core.change_intel import (
    analyze_change,
    capability_diff,
    migration_requirements,
    version_moves,
)
from forge_doctor.core.platform_graph import DataPlatformGraph, Entity, EntityKind


def _e(kind: EntityKind, domain: str, ident: str, file: str = "", **attrs: str) -> Entity:
    return Entity(
        kind=kind,
        domain=domain,
        identifier=ident,
        file=Path(file) if file else None,
        attrs=tuple(sorted(attrs.items())),
    )


def _glue(version: str | None = "4.0") -> Entity:
    attrs = {} if version is None else {"glue_version": version}
    return _e(EntityKind.COMPUTE_JOB, "glue", "orders-etl", "main.tf", **attrs)


def _graph(*entities: Entity) -> DataPlatformGraph:
    g = DataPlatformGraph()
    for e in entities:
        g.add_entity(e)
    return g


def _athena(engine_version: str) -> Entity:
    return _e(EntityKind.COMPUTE_JOB, "athena", "q", "main.tf", engine_version=engine_version)


def test_capability_transition_unsupported_to_supported():
    base = _graph(_athena("1"))
    head = _graph(_athena("2"))
    transitions = capability_diff(base, head)
    un = next(t for t in transitions if t.capability == "ATHENA_UNLOAD")
    assert un.before == CapabilityStatus.UNSUPPORTED.value
    assert un.after == CapabilityStatus.SUPPORTED.value
    assert un.platform == "athena"


def test_capability_transition_supported_to_unsupported():
    base = _graph(_athena("3"))
    head = _graph(_athena("1"))  # downgrade
    transitions = capability_diff(base, head)
    un = next(t for t in transitions if t.capability == "ATHENA_UNLOAD")
    assert un.before == CapabilityStatus.SUPPORTED.value
    assert un.after == CapabilityStatus.UNSUPPORTED.value


def test_unchanged_capability_not_reported():
    base = _graph(_glue("5.0"))
    head = _graph(_glue("5.0"))
    assert capability_diff(base, head) == ()


def test_no_version_move_no_requirements():
    base = _graph(_e(EntityKind.COMPUTE_JOB, "glue", "j", "f.py", worker_type="G.1X"))
    head = _graph(_e(EntityKind.COMPUTE_JOB, "glue", "j", "f.py", worker_type="G.2X"))
    assert version_moves(base, head) == ()
    assert migration_requirements(version_moves(base, head)) == ()


def test_unknown_version_never_fabricates():
    base = _graph(_glue("5.0"))
    head = _graph(_glue("99.9"))
    reqs = migration_requirements(version_moves(base, head))
    assert len(reqs) == 1
    assert reqs[0].status == "unknown"
    assert reqs[0].required_changes == ()
    assert reqs[0].blockers == ()
    # capabilities still evaluate - as unknown, not fabricated support
    lf = next(t for t in capability_diff(base, head) if t.capability == "LAKEFORMATION_FGAC")
    assert lf.after == CapabilityStatus.UNKNOWN.value


def test_glue_4_to_5_extracts_plan_steps():
    base = _graph(_glue("4.0"))
    head = _graph(_glue("5.0"))
    reqs = migration_requirements(version_moves(base, head))
    assert len(reqs) == 1
    req = reqs[0]
    assert req.status == "known"
    assert req.move.attr == "glue_version"
    assert req.move.from_version == "4.0"
    assert req.move.to_version == "5.0"
    assert any("Python 3.10" in c for c in req.required_changes)
    assert any("Java 8" in c for c in req.required_changes)
    assert req.blockers  # HIGH-severity entries are blockers


def test_deterministic_ordering():
    a = _e(EntityKind.COMPUTE_JOB, "glue", "b-job", "b.tf", glue_version="4.0")
    b = _e(EntityKind.COMPUTE_JOB, "glue", "a-job", "a.tf", glue_version="4.0")
    a2 = _e(EntityKind.COMPUTE_JOB, "glue", "b-job", "b.tf", glue_version="5.0")
    b2 = _e(EntityKind.COMPUTE_JOB, "glue", "a-job", "a.tf", glue_version="5.0")
    base = _graph(a, b)
    head = _graph(b2, a2)  # insertion order differs
    intel = analyze_change(base, head)
    entity_ids = [r.move.entity_id for r in intel.migration_requirements]
    assert entity_ids == sorted(entity_ids)
    caps = [(t.capability, t.platform) for t in intel.capability_transitions]
    assert caps == sorted(caps)
    # repeat analysis -> identical
    assert analyze_change(base, head) == intel


def test_analyze_change_empty_when_identical():
    g = _graph(_glue("5.0"))
    intel = analyze_change(g, g)
    assert intel.capability_transitions == ()
    assert intel.migration_requirements == ()


def test_removed_entity_capability_state():
    base = _graph(_glue("5.0"))
    head = _graph()
    transitions = capability_diff(base, head)
    lf = next(t for t in transitions if t.capability == "LAKEFORMATION_FGAC")
    assert lf.before == CapabilityStatus.SUPPORTED.value
    assert lf.after == "absent"
