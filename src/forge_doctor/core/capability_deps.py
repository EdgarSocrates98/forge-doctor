"""Capability dependency semantics — requires/lifecycle/readiness (spec 231).

The registry answers "does this platform support X"; this module answers
"can X actually be used" — because X may *require* Y (which requires Z),
be *replaced by* something newer, or have been *removed in* a version.

All dependency edges come from the versioned knowledge packs
(``requires``/``requires_any``/``alternatives``/``incompatible_with``/
``specializes`` + ``introduced_in``/``deprecated_in``/``removed_in``/
``replacement``); this module only traverses them. Missing dependency
facts degrade to UNKNOWN/PARTIAL — never silently unsupported.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from forge_doctor.core.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityStatus,
    DependencyFacts,
    _version_key,
)
from forge_doctor.core.platform_ontology import WorkloadIntent

if TYPE_CHECKING:
    from collections.abc import Iterable


class CapabilityRel(Enum):
    """Edge kinds between capabilities (declared in packs)."""

    REQUIRES = "requires"
    REQUIRES_ANY = "requires_any"
    ALTERNATIVE_TO = "alternative_to"
    INCOMPATIBLE_WITH = "incompatible_with"
    REPLACED_BY = "replaced_by"
    SPECIALIZES = "specializes"


class LifecycleStatus(Enum):
    """Where a capability sits in its lifecycle for a given version."""

    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    REMOVED = "removed"
    UNKNOWN = "unknown"


class CapabilityReadiness(Enum):
    """Whether a capability is usable end-to-end (deps included)."""

    READY = "ready"  # supported + every requirement provably satisfied
    PARTIAL = "partial"  # supported, but some requirement is unproven/conditional
    BLOCKED = "blocked"  # unsupported, or a hard requirement is unsupported/removed
    UNKNOWN = "unknown"  # cannot prove either way


@dataclass(frozen=True)
class DependencyEdge:
    """One declared edge between two capabilities."""

    rel: CapabilityRel
    src: str
    dst: str


@dataclass(frozen=True)
class DependencyStep:
    """One node in an evaluated chain."""

    capability: str
    rel: CapabilityRel | None  # how this node was reached (None = root)
    status: CapabilityStatus
    reason: str = ""


@dataclass(frozen=True)
class DependencyEvaluation:
    """Full readiness report for one (platform, capability)."""

    platform: str
    capability: str
    status: CapabilityStatus
    lifecycle: LifecycleStatus
    readiness: CapabilityReadiness
    replacement: str = ""
    blocked_path: tuple[DependencyStep, ...] = ()
    alternatives: tuple[DependencyStep, ...] = ()
    incompatibles: tuple[DependencyStep, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()
    missing: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.readiness is CapabilityReadiness.READY


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def lifecycle_status(facts: DependencyFacts, version: str | None) -> LifecycleStatus:
    """Lifecycle position of a capability for a platform version.

    No lifecycle fields → AVAILABLE (the pack asserts the capability
    exists). Gates present but no version → UNKNOWN (can't prove where
    in the lifecycle we are). ``removed_in`` wins over ``deprecated_in``
    when both apply.
    """
    if not (facts.introduced_in or facts.deprecated_in or facts.removed_in):
        return LifecycleStatus.AVAILABLE
    if version is None or not version:
        return LifecycleStatus.UNKNOWN
    if facts.removed_in and _version_key(version) >= _version_key(facts.removed_in):
        return LifecycleStatus.REMOVED
    if facts.deprecated_in and _version_key(version) >= _version_key(facts.deprecated_in):
        return LifecycleStatus.DEPRECATED
    if facts.introduced_in and _version_key(version) < _version_key(facts.introduced_in):
        return LifecycleStatus.UNKNOWN  # capability may not exist yet
    return LifecycleStatus.AVAILABLE


def dependency_edges(facts: DependencyFacts) -> tuple[DependencyEdge, ...]:
    """All declared edges for a capability, deterministic order."""
    edges: list[DependencyEdge] = []
    edges += [DependencyEdge(CapabilityRel.REQUIRES, facts.capability, d) for d in facts.requires]
    for group in facts.requires_any:
        edges += [DependencyEdge(CapabilityRel.REQUIRES_ANY, facts.capability, d) for d in group]
    edges += [
        DependencyEdge(CapabilityRel.ALTERNATIVE_TO, facts.capability, d)
        for d in facts.alternatives
    ]
    edges += [
        DependencyEdge(CapabilityRel.INCOMPATIBLE_WITH, facts.capability, d)
        for d in facts.incompatible_with
    ]
    edges += [
        DependencyEdge(CapabilityRel.REPLACED_BY, facts.capability, d)
        for d in ((facts.replacement,) if facts.replacement else ())
    ]
    edges += [
        DependencyEdge(CapabilityRel.SPECIALIZES, facts.capability, d) for d in facts.specializes
    ]
    return tuple(edges)


# ---------------------------------------------------------------------------
# Dependency evaluation
# ---------------------------------------------------------------------------


def _resolve(
    registry: CapabilityRegistry,
    capability: str,
    ctx: CapabilityContext,
    visiting: tuple[str, ...],
    rel: CapabilityRel | None,
    out: dict[str, list],
) -> tuple[DependencyStep, bool]:
    """DFS one capability node; returns ``(step, subtree_blocked)``.

    ``visiting`` is the current ancestor path (cycle detection); ``out``
    accumulates report pieces. ``subtree_blocked`` is true when this node
    or any hard requirement below it is provably unusable — that is what
    propagates transitive failure up the chain.
    """
    if capability in visiting:
        cycle = (*visiting[visiting.index(capability) :], capability)
        out["cycles"].append(cycle)
        return (
            DependencyStep(capability, rel, CapabilityStatus.UNKNOWN, "dependency cycle"),
            False,
        )
    facts = registry.dependencies(ctx.platform, capability)
    life = lifecycle_status(facts, ctx.version)
    if life is LifecycleStatus.REMOVED:
        status = CapabilityStatus.UNSUPPORTED
        reason = f"removed in {facts.removed_in}" + (
            f" — replaced by {facts.replacement}" if facts.replacement else ""
        )
    else:
        result = registry.evaluate(capability, ctx)
        status, reason = result.status, result.reason
    step = DependencyStep(capability, rel, status, reason)
    if status is CapabilityStatus.UNSUPPORTED:
        return step, True

    path = (*visiting, capability)
    blocked = False
    for dep in facts.requires:
        sub, sub_blocked = _resolve(registry, dep, ctx, path, CapabilityRel.REQUIRES, out)
        if sub_blocked:
            blocked = True
            out["blocked_paths"].append((path, sub))
    for group in facts.requires_any:
        subs: list[tuple[DependencyStep, bool]] = [
            _resolve(registry, dep, ctx, path, CapabilityRel.REQUIRES_ANY, out) for dep in group
        ]
        if not any(s.status is CapabilityStatus.SUPPORTED and not b for s, b in subs):
            blocked = True
            out["unsatisfied_groups"].append(tuple(s for s, _ in subs))
            for s, b in subs:
                if b:
                    out["blocked_paths"].append((path, s))
    for alt in facts.alternatives:
        alt_step, _ = _resolve(registry, alt, ctx, path, CapabilityRel.ALTERNATIVE_TO, out)
        out["alternatives"].append(alt_step)
    for inc in facts.incompatible_with:
        sub, _ = _resolve(registry, inc, ctx, path, CapabilityRel.INCOMPATIBLE_WITH, out)
        if sub.status is CapabilityStatus.SUPPORTED:
            out["incompatibles"].append(sub)
    for dep in (*facts.requires, *[d for g in facts.requires_any for d in g]):
        if dep not in registry.capabilities_for(ctx.platform):
            out["missing"].append(dep)
    return step, blocked


def evaluate_dependencies(
    registry: CapabilityRegistry,
    capability: str,
    context: CapabilityContext | None = None,
    **kwargs: object,
) -> DependencyEvaluation:
    """Resolve a capability's full dependency chain for a context.

    Readiness rules (deterministic):
    - REMOVED lifecycle, or own status UNSUPPORTED → BLOCKED (self).
    - any ``requires`` dep UNSUPPORTED/REMOVED, or a ``requires_any``
      group with no satisfied member → BLOCKED with the failing chain.
    - missing dep facts or unproven (UNKNOWN/CONDITIONAL) deps → PARTIAL.
    - own status UNKNOWN → UNKNOWN (deps can't make it provably ready).
    - everything proven → READY.
    """
    ctx = context or CapabilityContext(
        platform=str(kwargs.pop("platform", "")),
        attributes=tuple(sorted((str(k), str(v)) for k, v in kwargs.items())),
    )
    facts = registry.dependencies(ctx.platform, capability)
    life = lifecycle_status(facts, ctx.version)
    out: dict[str, list] = {
        "cycles": [],
        "blocked_paths": [],
        "unsatisfied_groups": [],
        "alternatives": [],
        "incompatibles": [],
        "missing": [],
    }
    root, blocked = _resolve(registry, capability, ctx, (), None, out)

    # Explainable blocked path: the first failing requirement's ancestor
    # chain plus the leaf that provably failed (X requires Y requires Z…).
    blocked_path: list[DependencyStep] = [root]
    if out["blocked_paths"]:
        chain_ids, leaf = out["blocked_paths"][0]
        for anc in chain_ids[1:]:  # chain_ids[0] is the root
            anc_result = registry.evaluate(anc, ctx)
            blocked_path.append(DependencyStep(anc, CapabilityRel.REQUIRES, anc_result.status))
        blocked_path.append(leaf)
    missing = tuple(sorted(set(out["missing"])))

    if (
        root.status is CapabilityStatus.UNSUPPORTED
        or life is LifecycleStatus.REMOVED
        or blocked
        or out["unsatisfied_groups"]
    ):
        readiness = CapabilityReadiness.BLOCKED
    elif root.status is CapabilityStatus.UNKNOWN:
        readiness = CapabilityReadiness.UNKNOWN
    elif missing or out["cycles"] or root.status is CapabilityStatus.CONDITIONAL:
        readiness = CapabilityReadiness.PARTIAL
    else:
        readiness = CapabilityReadiness.READY

    # dedupe cycles deterministically
    cycles = tuple(sorted({tuple(c) for c in out["cycles"]}))
    return DependencyEvaluation(
        platform=ctx.platform,
        capability=capability,
        status=root.status,
        lifecycle=life,
        readiness=readiness,
        replacement=facts.replacement,
        blocked_path=tuple(blocked_path),
        alternatives=tuple(sorted(set(out["alternatives"]), key=lambda s: s.capability)),
        incompatibles=tuple(sorted(set(out["incompatibles"]), key=lambda s: s.capability)),
        cycles=cycles,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# Workload → capability requirements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRequirement:
    """One requirement a workload places on a platform.

    ``any_of`` is a requires-any group: the requirement is satisfied when
    at least one listed capability is SUPPORTED. Group names are the
    abstract requirement (e.g. ``vector_similarity``); the ids are the
    concrete capability facts that can satisfy it.
    """

    name: str
    any_of: tuple[str, ...]
    reason: str = ""


_WORKLOAD_REQUIREMENTS: dict[WorkloadIntent, tuple[CapabilityRequirement, ...]] = {
    WorkloadIntent.VECTOR_SEARCH: (
        CapabilityRequirement(
            "vector_similarity",
            ("VECTOR_SEARCH_KNN",),
            "kNN/ANN search needs a vector index type.",
        ),
    ),
    WorkloadIntent.SEARCH_ANALYTICS: (
        CapabilityRequirement(
            "ingest",
            ("INGEST_PIPELINES",),
            "Document pipelines for search ingestion.",
        ),
    ),
    WorkloadIntent.EVENT_DRIVEN: (
        CapabilityRequirement(
            "replayable_source",
            ("KINESIS_REPLAYABLE_SOURCE", "KAFKA_REPLAYABLE_SOURCE"),
            "Event-driven pipelines need a replayable record source.",
        ),
    ),
    WorkloadIntent.TRANSFORMATION: (
        CapabilityRequirement(
            "row_mutations",
            ("DELTA_MERGE", "ICEBERG_UPDATE", "ICEBERG_MERGE_WRITE", "DML_MUTATIONS"),
            "In-place transformation needs row-level mutation support.",
        ),
    ),
    WorkloadIntent.GOVERNANCE: (
        CapabilityRequirement(
            "fine_grained_access",
            ("LAKEFORMATION_FGAC", "LAKEFORMATION_FTA"),
            "Governance needs fine-grained access control.",
        ),
    ),
    WorkloadIntent.BATCH_ANALYTICS: (
        CapabilityRequirement(
            "sql_execution",
            ("SQL_QUERY",),
            "Analytical SQL execution.",
        ),
        CapabilityRequirement(
            "scan_pruning",
            ("PARTITIONING", "CLUSTERING"),
            "Batch scans need partition/cluster pruning to stay economical.",
        ),
    ),
    WorkloadIntent.REALTIME_SERVING: (
        CapabilityRequirement(
            "low_latency_serving",
            ("RESULT_CACHE", "BI_ENGINE"),
            "Sub-second serving needs cached/precomputed results.",
        ),
    ),
}


def workload_requirements(intent: WorkloadIntent) -> tuple[CapabilityRequirement, ...]:
    return _WORKLOAD_REQUIREMENTS.get(intent, ())


@dataclass(frozen=True)
class RequirementResult:
    requirement: CapabilityRequirement
    satisfied_by: str = ""  # capability id that satisfied it, if any
    statuses: tuple[tuple[str, CapabilityStatus], ...] = ()

    @property
    def satisfied(self) -> bool:
        return bool(self.satisfied_by)


def evaluate_workload(
    registry: CapabilityRegistry,
    intent: WorkloadIntent,
    ctx: CapabilityContext,
) -> tuple[CapabilityReadiness, tuple[RequirementResult, ...]]:
    """Evaluate a workload's capability requirements on one platform.

    All requirements satisfied → READY; none evaluatable → UNKNOWN;
    any requirement with every member UNSUPPORTED → BLOCKED; else
    PARTIAL.
    """
    reqs = workload_requirements(intent)
    if not reqs:
        return CapabilityReadiness.UNKNOWN, ()
    results: list[RequirementResult] = []
    for req in reqs:
        statuses: list[tuple[str, CapabilityStatus]] = []
        satisfied_by = ""
        for cap in req.any_of:
            status = registry.evaluate(cap, ctx).status
            statuses.append((cap, status))
            if status is CapabilityStatus.SUPPORTED and not satisfied_by:
                satisfied_by = cap
        results.append(RequirementResult(req, satisfied_by, tuple(statuses)))
    if all(r.satisfied for r in results):
        return CapabilityReadiness.READY, tuple(results)
    if any(
        not r.satisfied and all(s is CapabilityStatus.UNSUPPORTED for _, s in r.statuses)
        for r in results
    ):
        return CapabilityReadiness.BLOCKED, tuple(results)
    if all(all(s is CapabilityStatus.UNKNOWN for _, s in r.statuses) for r in results):
        return CapabilityReadiness.UNKNOWN, tuple(results)
    return CapabilityReadiness.PARTIAL, tuple(results)


def all_edges(
    registry: CapabilityRegistry, platforms: Iterable[str] | None = None
) -> tuple[DependencyEdge, ...]:
    """Every declared capability edge in the packs (deterministic order)."""
    scope = sorted(set(platforms)) if platforms is not None else registry.platforms()
    edges: list[DependencyEdge] = []
    for platform in scope:
        for cap in registry.capabilities_for(platform):
            edges.extend(dependency_edges(registry.dependencies(platform, cap)))
    return tuple(sorted(set(edges), key=lambda e: (e.rel.value, e.src, e.dst)))
