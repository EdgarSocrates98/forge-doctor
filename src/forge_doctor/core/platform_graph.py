"""Canonical data-platform graph: entities + typed relationships.

The shared substrate for cross-domain reasoning (blast radius, lineage,
connected-data analysis). Every domain model is a producer; consumers
query the graph - domains never import each other.

Deliberately minimal: build + query + serialize. No query language, no
persistence, no graph-database dependency. Deterministic by
construction: entity ids are ``{kind}:{domain}:{identifier}`` where the
adapter picks the most canonical identifier available (ARN >
catalog-qualified name > path > bare name), and serialization sorts
everything.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

from forge_doctor.core.models import EvidenceKind


class EntityKind(Enum):
    """Canonical platform entity types (prompt_evo_neptune_graph_dynamo)."""

    WORKFLOW = "workflow"
    TASK = "task"
    COMPUTE_JOB = "compute_job"
    QUERY = "query"
    DATASET = "dataset"
    TABLE = "table"
    STREAM = "stream"
    CATALOG = "catalog"
    STORAGE_LOCATION = "storage_location"
    PRINCIPAL = "principal"
    INFRASTRUCTURE_RESOURCE = "infrastructure_resource"
    DATABASE = "database"
    GRAPH = "graph"
    GRAPH_NODE = "graph_node"
    GRAPH_EDGE = "graph_edge"
    REPO = "repo"
    CAPABILITY = "capability"
    KNOWLEDGE_PACK = "knowledge_pack"


class RelKind(Enum):
    """Canonical relationship types."""

    INVOKES = "INVOKES"
    READS = "READS"
    WRITES = "WRITES"
    DEFINES = "DEFINES"
    GOVERNS = "GOVERNS"
    STORED_IN = "STORED_IN"
    DEPENDS_ON = "DEPENDS_ON"
    TRIGGERS = "TRIGGERS"
    PRODUCES = "PRODUCES"
    CONSUMES = "CONSUMES"
    IMPLEMENTS = "IMPLEMENTS"
    EVIDENCED_BY = "EVIDENCED_BY"


@dataclass(frozen=True)
class Entity:
    """One platform entity. ``id`` is ``{kind}:{domain}:{identifier}``."""

    kind: EntityKind
    domain: str  # producing model: airflow | terraform | iceberg | ...
    identifier: str  # canonical: ARN > qualified name > path > bare name
    name: str = ""  # display name; defaults to the identifier
    file: Path | None = None
    line: int | None = None
    attrs: tuple[tuple[str, str], ...] = ()

    @property
    def id(self) -> str:
        return f"{self.kind.value}:{self.domain}:{self.identifier}"

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass(frozen=True)
class Relationship:
    """A typed src→dst edge carrying the evidence plane it came from."""

    src: str  # Entity.id
    dst: str  # Entity.id
    kind: RelKind
    evidence_kind: EvidenceKind | None = None
    attrs: tuple[tuple[str, str], ...] = ()

    def attr(self, key: str, default: str = "") -> str:
        return dict(self.attrs).get(key, default)


@dataclass
class DataPlatformGraph:
    """In-memory entity/relationship store with deterministic output."""

    _entities: dict[str, Entity] = field(default_factory=dict)
    _edges: set[Relationship] = field(default_factory=set)
    _out: dict[str, list[Relationship]] = field(default_factory=dict)
    _in: dict[str, list[Relationship]] = field(default_factory=dict)

    def add_entity(self, entity: Entity) -> Entity:
        """Insert; on collision the first occurrence wins per-field while
        missing attrs/file/line are filled from later producers - two
        adapters describing the same entity union their facts."""
        existing = self._entities.get(entity.id)
        if existing is None:
            self._entities[entity.id] = entity
            return entity
        merged_attrs = dict(entity.attrs)
        merged_attrs.update(existing.attrs)  # first producer wins per key
        if (
            tuple(sorted(merged_attrs.items())) == existing.attrs
            and (existing.file or not entity.file)
            and (existing.line is not None or entity.line is None)
            and (existing.name or not entity.name)
        ):
            return existing  # nothing new to merge - keep identity
        merged = replace(
            existing,
            file=existing.file or entity.file,
            line=existing.line if existing.line is not None else entity.line,
            attrs=tuple(sorted(merged_attrs.items())),
            name=existing.name or entity.name,
        )
        self._entities[entity.id] = merged
        return merged

    def add_relationship(self, rel: Relationship) -> bool:
        """Insert; returns False for an exact duplicate edge."""
        if rel.src not in self._entities or rel.dst not in self._entities:
            raise KeyError(f"edge references unknown entity: {rel.src} -> {rel.dst}")
        if rel in self._edges:
            return False
        self._edges.add(rel)
        self._out.setdefault(rel.src, []).append(rel)
        self._in.setdefault(rel.dst, []).append(rel)
        return True

    # -- queries -----------------------------------------------------------
    def entity(self, entity_id: str) -> Entity | None:
        return self._entities.get(entity_id)

    def entities(self, kind: EntityKind | None = None) -> list[Entity]:
        if kind is None:
            return sorted(self._entities.values(), key=lambda e: e.id)
        return [e for e in self.entities() if e.kind is kind]

    def relationships(self, kind: RelKind | None = None) -> list[Relationship]:
        edges = sorted(self._edges, key=lambda r: (r.src, r.dst, r.kind.value))
        if kind is None:
            return edges
        return [r for r in edges if r.kind is kind]

    def outbound(self, entity_id: str, kind: RelKind | None = None) -> list[Relationship]:
        return self._select(self._out, entity_id, kind)

    def inbound(self, entity_id: str, kind: RelKind | None = None) -> list[Relationship]:
        return self._select(self._in, entity_id, kind)

    def neighbors(self, entity_id: str) -> set[str]:
        out = {r.dst for r in self._out.get(entity_id, [])}
        return out | {r.src for r in self._in.get(entity_id, [])}

    def reachable(self, entity_id: str, direction: str = "out") -> set[str]:
        """Cycle-safe BFS over outbound (or inbound) edges."""
        adjacency = self._in if direction == "in" else self._out
        seen: set[str] = set()
        queue: deque[str] = deque([entity_id])
        while queue:
            current = queue.popleft()
            for rel in adjacency.get(current, []):
                nxt = rel.src if direction == "in" else rel.dst
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        seen.discard(entity_id)
        return seen

    @staticmethod
    def _select(
        index: dict[str, list[Relationship]], entity_id: str, kind: RelKind | None
    ) -> list[Relationship]:
        rels = index.get(entity_id, [])
        if kind is None:
            return list(rels)
        return [r for r in rels if r.kind is kind]

    # -- serialization -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Stable JSON-ready form: entities and edges sorted, always."""
        return {
            "entities": [
                {
                    "id": e.id,
                    "kind": e.kind.value,
                    "domain": e.domain,
                    "identifier": e.identifier,
                    "name": e.name or e.identifier,
                    **({"file": e.file.as_posix()} if e.file is not None else {}),
                    **({"line": e.line} if e.line is not None else {}),
                    **({"attrs": dict(sorted(e.attrs))} if e.attrs else {}),
                }
                for e in self.entities()
            ],
            "relationships": [
                {
                    "src": r.src,
                    "dst": r.dst,
                    "kind": r.kind.value,
                    **(
                        {"evidence_kind": r.evidence_kind.value}
                        if r.evidence_kind is not None
                        else {}
                    ),
                    **({"attrs": dict(sorted(r.attrs))} if r.attrs else {}),
                }
                for r in self.relationships()
            ],
        }


def entities_of(graph: DataPlatformGraph, ids: Iterable[str]) -> list[Entity]:
    """Resolve ids to entities (skips unknown) - blast-radius display."""
    return [e for i in ids if (e := graph.entity(i)) is not None]
