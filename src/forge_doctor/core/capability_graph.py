"""Capability graph: provenance wiring from capabilities to evidence.

Every evaluated capability becomes a ``capability`` entity linked
``EVIDENCED_BY`` to the knowledge-pack entity whose entry decided its
status — and, when a project graph is supplied, to the platform entities
whose version attrs provided the evaluation context. Provenance is
derived from what the evaluator actually read, never asserted.
"""

from __future__ import annotations

from forge_doctor.core.capabilities import (
    CapabilityContext,
    CapabilityRegistry,
    CapabilityResult,
)
from forge_doctor.core.change_intel import _entity_version, platform_versions
from forge_doctor.core.platform_graph import (
    DataPlatformGraph,
    Entity,
    EntityKind,
    Relationship,
    RelKind,
)


def _provenance_attrs(result: CapabilityResult) -> tuple[tuple[str, str], ...]:
    attrs: dict[str, str] = {
        "status": result.status.value,
        "entry_id": result.entry_id,
    }
    if result.matched_when:
        attrs["matched_when"] = ",".join(f"{k}={v}" for k, v in result.matched_when)
    for i, missing in enumerate(result.missing_evidence):
        attrs[f"missing_evidence.{i}"] = missing
    if result.source:
        attrs["source"] = result.source
    return tuple(sorted(attrs.items()))


def capability_subgraph(
    graph: DataPlatformGraph,
    registry: CapabilityRegistry | None = None,
) -> DataPlatformGraph:
    """Build the capability→evidence subgraph for a project's platform graph.

    Scope: platforms observed as entity domains in ``graph``. Capabilities
    evaluate against the per-domain version the graph's entities imply —
    the version-source entities join the subgraph as evidence.
    """
    registry = registry or CapabilityRegistry()
    versions = platform_versions(graph)
    domains = sorted({e.domain for e in graph.entities()})
    out = DataPlatformGraph()
    for domain in domains:
        caps = registry.capabilities_for(domain)
        if not caps:
            continue
        version = versions.get(domain)
        pack = Entity(
            kind=EntityKind.KNOWLEDGE_PACK,
            domain="knowledge",
            identifier=f"capabilities/{domain}",
            name=f"capabilities/{domain}",
        )
        out.add_entity(pack)
        # Entities whose version attr supplied the evaluation context.
        version_sources = [
            e
            for e in graph.entities()
            if e.domain == domain
            and version is not None
            and _entity_version(dict(e.attrs)) == version
        ]
        for cap in caps:
            result = registry.evaluate(cap, CapabilityContext(platform=domain, version=version))
            cap_ent = out.add_entity(
                Entity(
                    kind=EntityKind.CAPABILITY,
                    domain=domain,
                    identifier=cap,
                    name=f"{domain}/{cap}",
                    attrs=(
                        ("status", result.status.value),
                        *((("version_context", version),) if version is not None else ()),
                    ),
                )
            )
            out.add_relationship(
                Relationship(
                    src=cap_ent.id,
                    dst=pack.id,
                    kind=RelKind.EVIDENCED_BY,
                    attrs=_provenance_attrs(result),
                )
            )
            for src_ent in version_sources:
                out.add_entity(src_ent)
                out.add_relationship(
                    Relationship(
                        src=cap_ent.id,
                        dst=src_ent.id,
                        kind=RelKind.EVIDENCED_BY,
                        evidence_kind=None,
                        attrs=(("provides", "version"), ("value", version or "")),
                    )
                )
            # Dependency edges declared by the pack (spec 231): capability
            # -> DEPENDS_ON -> required/alternative capability, carrying
            # the CapabilityRel flavour as an attr (the platform-graph
            # vocabulary stays unchanged).
            from forge_doctor.core.capability_deps import dependency_edges

            for edge in dependency_edges(registry.dependencies(domain, cap)):
                dep_ent = out.add_entity(
                    Entity(
                        kind=EntityKind.CAPABILITY,
                        domain=domain,
                        identifier=edge.dst,
                        name=f"{domain}/{edge.dst}",
                    )
                )
                out.add_relationship(
                    Relationship(
                        src=cap_ent.id,
                        dst=dep_ent.id,
                        kind=RelKind.DEPENDS_ON,
                        attrs=(("capability_rel", edge.rel.value),),
                    )
                )
    return out
