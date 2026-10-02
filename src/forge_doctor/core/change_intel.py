"""Change intelligence on top of the semantic entity diff.

Two additions to ``diff --semantic``:

1. **Capability diff** — capability-registry status transitions between
   the base and head refs' observed environments (e.g. a glue_version
   4.0 -> 5.0 move flips ICEBERG_MERGE_WRITE unsupported -> supported).
2. **Migration requirements** — version moves in entity attrs
   (``*_version``/``runtime``/``dbr``...) are matched against the
   ``<domain>/compatibility`` knowledge packs so each move lists the
   target-version required changes and HIGH-severity blockers.

Everything is deterministic and read-only: no parallel compat logic —
the knowledge packs are the single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from forge_doctor.core.knowledge import load_pack

if TYPE_CHECKING:
    from forge_doctor.core.platform_graph import DataPlatformGraph

# Attrs that carry a runtime/version signal on entities. Ordered by
# specificity; the first non-empty wins per entity.
VERSION_ATTRS = (
    "version",
    "glue_version",
    "runtime_version",
    "engine_version",
    "python_version",
    "node_version",
    "dbr",
    "databricks_runtime",
    "runtime",
    "format_version",
)


@dataclass(frozen=True)
class CapabilityTransition:
    """One capability whose status changed between refs."""

    capability: str
    platform: str
    before: str
    after: str


@dataclass(frozen=True)
class VersionMove:
    """An entity attribute that moved versions between refs."""

    entity_id: str
    domain: str
    attr: str
    from_version: str
    to_version: str


@dataclass(frozen=True)
class MigrationRequirement:
    """Requirements for one observed version move."""

    move: VersionMove
    status: str  # "known" | "unknown" (no compatibility data)
    required_changes: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChangeIntelligence:
    capability_transitions: tuple[CapabilityTransition, ...] = ()
    migration_requirements: tuple[MigrationRequirement, ...] = field(default_factory=tuple)


def _entity_version(entity_attrs: dict[str, str]) -> str | None:
    for key in VERSION_ATTRS:
        value = entity_attrs.get(key)
        if value:
            return value
    return None


def platform_versions(graph: DataPlatformGraph) -> dict[str, str]:
    """domain -> version: the most common version-attr value across that
    domain's entities (deterministic: count desc, then lexicographic)."""
    counts: dict[str, dict[str, int]] = {}
    for ent in graph.entities():
        version = _entity_version(dict(ent.attrs))
        if not version:
            continue
        counts.setdefault(ent.domain, {}).setdefault(version, 0)
        counts[ent.domain][version] += 1
    return {
        domain: min(versions.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        for domain, versions in counts.items()
        if versions
    }


def capability_states(graph: DataPlatformGraph) -> dict[str, str]:
    """``platform/CAP -> status`` evaluated against this graph's observed
    per-domain versions."""
    from forge_doctor.core.capabilities import CapabilityContext, capability_registry

    registry = capability_registry()
    versions = platform_versions(graph)
    states: dict[str, str] = {}
    for domain in sorted({e.domain for e in graph.entities()}):
        for cap in registry.capabilities_for(domain):
            result = registry.evaluate(
                cap,
                CapabilityContext(
                    platform=domain,
                    version=versions.get(domain),
                ),
            )
            states[f"{domain}/{cap}"] = result.status.value
    return states


def capability_diff(
    base: DataPlatformGraph, head: DataPlatformGraph
) -> tuple[CapabilityTransition, ...]:
    """Status transitions between the two refs' capability states."""
    before = capability_states(base)
    after = capability_states(head)
    transitions: list[CapabilityTransition] = []
    for key in sorted(set(before) | set(after)):
        b = before.get(key, "absent")
        a = after.get(key, "absent")
        if b == a:
            continue
        platform, _, cap = key.partition("/")
        transitions.append(
            CapabilityTransition(capability=cap, platform=platform, before=b, after=a)
        )
    return tuple(sorted(transitions, key=lambda t: (t.capability, t.platform)))


def version_moves(base: DataPlatformGraph, head: DataPlatformGraph) -> tuple[VersionMove, ...]:
    """Entities present in both graphs whose version-ish attrs differ."""
    moves: list[VersionMove] = []
    head_by_id = {e.id: e for e in head.entities()}
    for ent in base.entities():
        other = head_by_id.get(ent.id)
        if other is None:
            continue
        a, b = dict(ent.attrs), dict(other.attrs)
        for key in sorted(set(a) | set(b)):
            is_version = key in VERSION_ATTRS or key.endswith("_version")
            if not is_version:
                continue
            va, vb = str(a.get(key, "")), str(b.get(key, ""))
            if va != vb:
                moves.append(
                    VersionMove(
                        entity_id=ent.id,
                        domain=ent.domain,
                        attr=key,
                        from_version=va or "-",
                        to_version=vb or "-",
                    )
                )
    return tuple(sorted(moves, key=lambda m: (m.entity_id, m.attr)))


def migration_requirements(
    moves: tuple[VersionMove, ...],
) -> tuple[MigrationRequirement, ...]:
    """Match each version move against the ``<domain>/compatibility``
    knowledge pack's ``targets`` map. Absent coverage is reported
    ``unknown`` — never fabricated."""
    out: list[MigrationRequirement] = []
    for move in moves:
        targets = load_pack(move.domain, "compatibility").get("targets") or {}
        notes = targets.get(move.to_version) or {}
        changes = [c for c in (notes.get("changes") or []) if isinstance(c, dict)]
        required = tuple(f"{c.get('change')}: {c.get('detail', '')}".rstrip(": ") for c in changes)
        blockers = tuple(
            r
            for r, c in zip(required, changes, strict=True)
            if str(c.get("severity", "")).upper() == "HIGH"
        )
        status = "known" if required else "unknown"
        out.append(
            MigrationRequirement(
                move=move, status=status, required_changes=required, blockers=blockers
            )
        )
    return tuple(out)


def analyze_change(base: DataPlatformGraph, head: DataPlatformGraph) -> ChangeIntelligence:
    """Full change-intelligence bundle for a base->head diff."""
    return ChangeIntelligence(
        capability_transitions=capability_diff(base, head),
        migration_requirements=migration_requirements(version_moves(base, head)),
    )
