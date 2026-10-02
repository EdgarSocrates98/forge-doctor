"""Capability dependency semantics: requires, lifecycle, readiness (spec 231)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from forge_doctor.core.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityStatus,
)
from forge_doctor.core.capability_deps import (
    CapabilityReadiness,
    CapabilityRel,
    LifecycleStatus,
    all_edges,
    dependency_edges,
    evaluate_dependencies,
    evaluate_workload,
    lifecycle_status,
    workload_requirements,
)
from forge_doctor.core.platform_ontology import WorkloadIntent

SRC = "https://example.com/docs"


def _pack(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "pack_version": "1.0.0",
        "verified_at": "2026-01-01",
        "capabilities": entries,
    }


def _entry(cap: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": cap,
        "status": kw.pop("status", "supported"),
        "source": SRC,
        **kw,
    }


def _registry(entries: list[dict[str, Any]], platform: str = "plat") -> CapabilityRegistry:
    return CapabilityRegistry(packs=[(platform, f"capabilities/{platform}", _pack(entries))])


CTX = CapabilityContext(platform="plat")


# --- pack parsing -----------------------------------------------------------------


def test_dependency_fields_parse() -> None:
    reg = _registry(
        [
            _entry(
                "A",
                requires=["B"],
                requires_any=[["C", "D"]],
                alternatives=["E"],
                incompatible_with=["F"],
                specializes=["G"],
                introduced_in="2.0",
                deprecated_in="5.0",
                removed_in="7.0",
                replacement="H",
            )
        ]
    )
    facts = reg.dependencies("plat", "A")
    assert facts.requires == ("B",)
    assert facts.requires_any == (("C", "D"),)
    assert facts.alternatives == ("E",)
    assert facts.incompatible_with == ("F",)
    assert facts.specializes == ("G",)
    assert (facts.introduced_in, facts.deprecated_in, facts.removed_in) == ("2.0", "5.0", "7.0")
    assert facts.replacement == "H"


def test_dependency_fields_optional_and_backwards_compatible() -> None:
    reg = _registry([_entry("A")])
    facts = reg.dependencies("plat", "A")
    assert facts.requires == () and facts.requires_any == ()
    assert reg.validation_issues == ()


def test_invalid_dependency_fields_flagged() -> None:
    reg = _registry([_entry("A", requires="B")])
    assert any("requires" in i for i in reg.validation_issues)
    reg2 = _registry([_entry("A", requires_any="B")])
    assert any("requires_any" in i for i in reg2.validation_issues)


# --- lifecycle ---------------------------------------------------------------------


def test_lifecycle_no_fields_available() -> None:
    facts = _registry([_entry("A")]).dependencies("plat", "A")
    assert lifecycle_status(facts, None) is LifecycleStatus.AVAILABLE


def test_lifecycle_gates() -> None:
    facts = _registry(
        [_entry("A", introduced_in="2.0", deprecated_in="5.0", removed_in="7.0")]
    ).dependencies("plat", "A")
    assert lifecycle_status(facts, None) is LifecycleStatus.UNKNOWN
    assert lifecycle_status(facts, "1.0") is LifecycleStatus.UNKNOWN
    assert lifecycle_status(facts, "3.0") is LifecycleStatus.AVAILABLE
    assert lifecycle_status(facts, "5.0") is LifecycleStatus.DEPRECATED
    assert lifecycle_status(facts, "7.0") is LifecycleStatus.REMOVED


# --- transitive requires --------------------------------------------------------------


def test_transitive_requires_blocks() -> None:
    reg = _registry(
        [
            _entry("X", requires=["Y"]),
            _entry("Y", requires=["Z"]),
            _entry("Z", status="unsupported", reason="Z unsupported on plat"),
        ]
    )
    ev = evaluate_dependencies(reg, "X", CTX)
    assert ev.readiness is CapabilityReadiness.BLOCKED
    chain = [s.capability for s in ev.blocked_path]
    assert chain == ["X", "Y", "Z"], chain
    assert ev.blocked_path[-1].status is CapabilityStatus.UNSUPPORTED


def test_requires_satisfied_ready() -> None:
    reg = _registry([_entry("X", requires=["Y"]), _entry("Y")])
    ev = evaluate_dependencies(reg, "X", CTX)
    assert ev.readiness is CapabilityReadiness.READY


# --- requires_any ----------------------------------------------------------------------


def test_requires_any_survives_on_one_alternative() -> None:
    reg = _registry(
        [
            _entry("X", requires_any=[["Y", "Z"]]),
            _entry("Y", status="unsupported"),
            _entry("Z"),
        ]
    )
    ev = evaluate_dependencies(reg, "X", CTX)
    assert ev.readiness is CapabilityReadiness.READY


def test_requires_any_all_unsupported_blocks() -> None:
    reg = _registry(
        [
            _entry("X", requires_any=[["Y", "Z"]]),
            _entry("Y", status="unsupported"),
            _entry("Z", status="unsupported"),
        ]
    )
    ev = evaluate_dependencies(reg, "X", CTX)
    assert ev.readiness is CapabilityReadiness.BLOCKED


# --- cycles -----------------------------------------------------------------------------


def test_cycle_detected_and_reported() -> None:
    reg = _registry([_entry("X", requires=["Y"]), _entry("Y", requires=["X"])])
    ev = evaluate_dependencies(reg, "X", CTX)
    assert ev.cycles, "cycle X<->Y should be reported"
    flat = {c for cycle in ev.cycles for c in cycle}
    assert {"X", "Y"} <= flat
    assert ev.readiness in (CapabilityReadiness.PARTIAL, CapabilityReadiness.READY)


def test_cycle_terminates() -> None:
    reg = _registry(
        [_entry("A", requires=["B"]), _entry("B", requires=["C"]), _entry("C", requires=["A"])]
    )
    ev = evaluate_dependencies(reg, "A", CTX)
    assert ev.cycles


# --- lifecycle-in-evaluation -------------------------------------------------------------


def test_removed_capability_blocks_with_replacement() -> None:
    reg = _registry([_entry("OLD", removed_in="7.0", replacement="NEW"), _entry("NEW")])
    ev = evaluate_dependencies(reg, "OLD", CapabilityContext(platform="plat", version="8.0"))
    assert ev.readiness is CapabilityReadiness.BLOCKED
    assert ev.lifecycle is LifecycleStatus.REMOVED
    assert ev.replacement == "NEW"
    assert "removed in 7.0" in ev.blocked_path[0].reason


def test_deprecated_still_usable() -> None:
    reg = _registry([_entry("A", deprecated_in="5.0", replacement="B"), _entry("B")])
    ev = evaluate_dependencies(reg, "A", CapabilityContext(platform="plat", version="6.0"))
    assert ev.lifecycle is LifecycleStatus.DEPRECATED
    assert ev.readiness is CapabilityReadiness.READY


# --- missing evidence ---------------------------------------------------------------------


def test_missing_dependency_facts_partial_not_blocked() -> None:
    reg = _registry([_entry("X", requires=["GHOST_CAP"])])
    ev = evaluate_dependencies(reg, "X", CTX)
    assert "GHOST_CAP" in ev.missing
    assert ev.readiness is CapabilityReadiness.PARTIAL


def test_unknown_self_status_not_ready() -> None:
    reg = _registry([_entry("X", requires=["Y"]), _entry("Y")])
    ev = evaluate_dependencies(reg, "OTHER", CTX)  # no facts for OTHER
    assert ev.readiness is CapabilityReadiness.UNKNOWN


# --- conditional capabilities --------------------------------------------------------------


def test_conditional_capability_partial() -> None:
    reg = _registry(
        [
            _entry(
                "X",
                conditions=[{"attribute": "flag", "op": "eq", "value": "on"}],
            )
        ]
    )
    ev = evaluate_dependencies(reg, "X", CTX)  # flag absent -> conditional
    assert ev.status is CapabilityStatus.CONDITIONAL
    assert ev.readiness is CapabilityReadiness.PARTIAL
    ev_on = evaluate_dependencies(
        reg, "X", CapabilityContext(platform="plat", attributes=(("flag", "on"),))
    )
    assert ev_on.readiness is CapabilityReadiness.READY


# --- incompatible / alternatives ------------------------------------------------------------------


def test_incompatible_supported_surfaces() -> None:
    reg = _registry([_entry("X", incompatible_with=["Y"]), _entry("Y")])
    ev = evaluate_dependencies(reg, "X", CTX)
    assert any(s.capability == "Y" for s in ev.incompatibles)


def test_alternatives_listed() -> None:
    reg = _registry([_entry("X", alternatives=["Y"]), _entry("Y")])
    ev = evaluate_dependencies(reg, "X", CTX)
    assert [s.capability for s in ev.alternatives] == ["Y"]
    assert ev.alternatives[0].status is CapabilityStatus.SUPPORTED


# --- determinism ----------------------------------------------------------------------------------


def test_deterministic_traversal() -> None:
    entries = [
        _entry("X", requires=["B", "A"], alternatives=["C"], incompatible_with=["D"]),
        _entry("A", requires=["C"]),
        _entry("B"),
        _entry("C"),
        _entry("D"),
    ]
    reg = _registry(entries)
    a = evaluate_dependencies(reg, "X", CTX)
    b = evaluate_dependencies(reg, "X", CTX)
    assert a == b
    edges = all_edges(reg)
    assert edges == tuple(sorted(set(edges), key=lambda e: (e.rel.value, e.src, e.dst)))


def test_dependency_edges_cover_all_rels() -> None:
    reg = _registry(
        [
            _entry(
                "X",
                requires=["A"],
                requires_any=[["B"]],
                alternatives=["C"],
                incompatible_with=["D"],
                replacement="E",
                specializes=["F"],
            )
        ]
    )
    rels = {e.rel for e in dependency_edges(reg.dependencies("plat", "X"))}
    assert rels == set(CapabilityRel)


# --- workload -> capability mapping -----------------------------------------------------


def test_workload_requirements_known_intents() -> None:
    reqs = workload_requirements(WorkloadIntent.VECTOR_SEARCH)
    assert reqs and "VECTOR_SEARCH_KNN" in reqs[0].any_of
    assert workload_requirements(WorkloadIntent.GRAPH_ANALYTICS) == ()


def test_evaluate_workload_ready_and_blocked() -> None:
    reg = _registry([_entry("VECTOR_SEARCH_KNN")], platform="plat")
    ready, results = evaluate_workload(reg, WorkloadIntent.VECTOR_SEARCH, CTX)
    assert ready is CapabilityReadiness.READY
    assert results[0].satisfied_by == "VECTOR_SEARCH_KNN"

    reg2 = _registry([_entry("VECTOR_SEARCH_KNN", status="unsupported")])
    blocked, _ = evaluate_workload(reg2, WorkloadIntent.VECTOR_SEARCH, CTX)
    assert blocked is CapabilityReadiness.BLOCKED

    reg3 = _registry([_entry("OTHER")])
    unknown, _ = evaluate_workload(reg3, WorkloadIntent.VECTOR_SEARCH, CTX)
    assert unknown is CapabilityReadiness.UNKNOWN


# --- bundled packs: dependency fields must still validate -------------------------------


def test_bundled_packs_no_validation_issues() -> None:
    reg = CapabilityRegistry()
    assert reg.validation_issues == ()


def test_bundled_dep_edges_resolve() -> None:
    """Real pack entries carrying dependency fields must evaluate."""
    reg = CapabilityRegistry()
    ev = evaluate_dependencies(reg, "DYNAMODB_LSI", CapabilityContext(platform="dynamodb"))
    assert ev.alternatives  # DYNAMODB_GSI
    ev2 = evaluate_dependencies(reg, "FEDERATED_WRITE", CapabilityContext(platform="trino"))
    assert ev2.readiness in (
        CapabilityReadiness.READY,
        CapabilityReadiness.PARTIAL,
        CapabilityReadiness.BLOCKED,
    )


def test_json_serializable_evaluation() -> None:
    reg = _registry([_entry("X", requires=["Y"]), _entry("Y")])
    ev = evaluate_dependencies(reg, "X", CTX)
    payload = {
        "capability": ev.capability,
        "status": ev.status.value,
        "readiness": ev.readiness.value,
        "lifecycle": ev.lifecycle.value,
        "blocked_path": [
            {"capability": s.capability, "status": s.status.value} for s in ev.blocked_path
        ],
        "missing": list(ev.missing),
    }
    json.dumps(payload)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
